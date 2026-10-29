/* LevelDB's encoding work on a batch of writes, with LevelDB's own code
 * (coding.c, hash.c): what a Put goes through on the way to an SSTable and
 * back.
 *   - write batch: tag byte, length-prefixed key and value (db/write_batch.cc)
 *   - memtable entry: VarintLength sizing, varint key length, key, fixed64
 *     sequence/type tag, varint value length, value (db/memtable.cc)
 *   - data block: prefix-compressed keys, varint shared/non-shared/value
 *     lengths, restarts every 16 keys (table/block_builder.cc), then parsed
 *     back entry by entry (table/block.cc)
 *   - bloom filter over the keys, probed with every key and as many absent
 *     ones (util/bloom.cc)
 *   - version edit: varint64 file numbers and sizes (db/version_edit.cc)
 * Keys are db_bench's 16-digit keys, values 0-400 bytes.
 *
 *   ldb_bench           a readable summary
 *   ldb_bench --check   deterministic output only (sizes, hashes, bloom counts)
 *   ldb_bench --time    one number, ns per record: oqed's bench command
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "../coding.h"

#define N 4096
#define REPS 40

static char keys[N][17];
static char values[N][400];
static uint32_t vlen[N];
static Slice key_slices[N], absent[N];
static char absent_keys[N][17];
static uint64_t xs = 0x9e3779b97f4a7c15ull;
static int hash_output; /* hash every buffer: --check and the first run only, not timed */

static uint64_t rnd(void)
{
	xs ^= xs << 13, xs ^= xs >> 7, xs ^= xs << 17;
	return xs;
}

static uint64_t fnv(uint64_t h, const char *p, size_t n)
{
	for (size_t i = 0; i < n; i++) h = (h ^ (uint8_t) p[i]) * 0x100000001b3ull;
	return h;
}

static void setup(void)
{
	uint64_t k = 0;
	for (int i = 0; i < N; i++) {
		k += 1 + rnd() % 64; /* sorted, as a flush writes them */
		snprintf(keys[i], sizeof keys[i], "%016llu", (unsigned long long) k);
		snprintf(absent_keys[i], sizeof absent_keys[i], "%016llu", (unsigned long long) (k * 7 + 3));
		key_slices[i] = (Slice) {keys[i], 16};
		absent[i] = (Slice) {absent_keys[i], 16};
		uint64_t r = rnd();
		vlen[i] = r % 8 == 0 ? r % 400 : r % 120; /* mostly short values, some long */
		for (uint32_t j = 0; j < vlen[i]; j++) values[i][j] = 'a' + (rnd() % 26);
	}
}

typedef struct {
	uint64_t hash;
	size_t batch, mem, block, filter, edit;
	int found, false_pos, parsed;
} Result;

static Result once(void)
{
	static Buf batch, mem, block, filter, edit;
	Result res = {0xcbf29ce484222325ull};
	batch.size = mem.size = block.size = filter.size = edit.size = 0;

	/* write batch */
	PutFixed64(&batch, 1000);
	PutFixed32(&batch, N);
	for (int i = 0; i < N; i++) {
		buf_push(&batch, 1); /* kTypeValue */
		PutLengthPrefixedSlice(&batch, key_slices[i]);
		PutLengthPrefixedSlice(&batch, (Slice) {values[i], vlen[i]});
	}
	/* replay it into the memtable */
	Slice in = {batch.data + 12, batch.size - 12};
	uint64_t seq = 1000;
	for (int i = 0; i < N; i++) {
		Slice key, value;
		in.data++, in.size--;
		if (!GetLengthPrefixedSlice(&in, &key) || !GetLengthPrefixedSlice(&in, &value)) abort();
		size_t ikey = key.size + 8;
		size_t len = VarintLength(ikey) + ikey + VarintLength(value.size) + value.size;
		size_t at = mem.size;
		buf_resize(&mem, at + len);
		char *p = EncodeVarint32(mem.data + at, ikey);
		memcpy(p, key.data, key.size);
		p += key.size;
		EncodeFixed64(p, (seq++ << 8) | 1);
		p += 8;
		p = EncodeVarint32(p, value.size);
		memcpy(p, value.data, value.size);
		if ((size_t) (p + value.size - mem.data) != at + len) abort();
	}

	/* data block */
	Buf restarts = {0};
	const char *last = "";
	size_t last_len = 0;
	for (int i = 0; i < N; i++) {
		size_t shared = 0;
		if (i % 16 == 0) {
			PutFixed32(&restarts, block.size);
		} else {
			while (shared < last_len && shared < 16 && last[shared] == keys[i][shared]) shared++;
		}
		PutVarint32(&block, shared);
		PutVarint32(&block, 16 - shared);
		PutVarint32(&block, vlen[i]);
		buf_append(&block, keys[i] + shared, 16 - shared);
		buf_append(&block, values[i], vlen[i]);
		last = keys[i], last_len = 16;
	}
	size_t data_end = block.size;
	buf_append(&block, restarts.data, restarts.size);
	PutFixed32(&block, restarts.size / 4);
	free(restarts.data);
	/* and parsed back */
	char cur[16];
	const char *p = block.data, *limit = block.data + data_end;
	while (p < limit) {
		uint32_t shared, non_shared, value_length;
		if (!(p = GetVarint32Ptr(p, limit, &shared)) || !(p = GetVarint32Ptr(p, limit, &non_shared)) ||
		    !(p = GetVarint32Ptr(p, limit, &value_length)))
			abort();
		memcpy(cur + shared, p, non_shared);
		if (memcmp(cur, keys[res.parsed], 16) != 0) abort();
		res.hash += value_length;
		p += non_shared + value_length;
		res.parsed++;
	}

	/* bloom filter */
	Bloom policy = bloom_policy(10);
	CreateFilter(&policy, key_slices, N, &filter);
	Slice f = {filter.data, filter.size};
	for (int i = 0; i < N; i++) {
		res.found += KeyMayMatch(&key_slices[i], &f);
		res.false_pos += KeyMayMatch(&absent[i], &f);
	}

	/* version edit: one file per 64 records */
	for (int i = 0; i < N / 64; i++) {
		PutVarint32(&edit, 7); /* kNewFile */
		PutVarint32(&edit, i % 7);
		PutVarint64(&edit, 100000 + i);
		PutVarint64(&edit, (uint64_t) mem.size * (i + 1) * 977);
		PutLengthPrefixedSlice(&edit, key_slices[i * 64]);
	}
	Slice e = {edit.data, edit.size};
	for (int i = 0; i < N / 64; i++) {
		uint32_t tag, level;
		uint64_t number, size;
		Slice smallest;
		if (!GetVarint32(&e, &tag) || !GetVarint32(&e, &level) || !GetVarint64(&e, &number) ||
		    !GetVarint64(&e, &size) || !GetLengthPrefixedSlice(&e, &smallest))
			abort();
		res.hash += size + number + level + tag + smallest.size;
	}
	res.batch = batch.size, res.mem = mem.size, res.block = block.size, res.filter = filter.size;
	res.edit = edit.size;
	if (!hash_output) return res;

	res.hash = fnv(res.hash, batch.data, batch.size);
	res.hash = fnv(res.hash, mem.data, mem.size);
	res.hash = fnv(res.hash, block.data, block.size);
	res.hash = fnv(res.hash, filter.data, filter.size);
	res.hash = fnv(res.hash, edit.data, edit.size);
	return res;
}

static double now(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec * 1e-9;
}

int main(int argc, char **argv)
{
	int check = argc > 1 && strcmp(argv[1], "--check") == 0;
	int time_only = argc > 1 && strcmp(argv[1], "--time") == 0;
	setup();
	hash_output = 1;
	Result r = once();
	hash_output = 0;
	Result plain = once();
	if (r.found != N || r.parsed != N) return fprintf(stderr, "bloom or block wrong\n"), 1;
	if (check) {
		printf("batch %zu mem %zu block %zu filter %zu edit %zu  hash %016llx  bloom %d/%d false %d\n", r.batch,
		       r.mem, r.block, r.filter, r.edit, (unsigned long long) r.hash, r.found, N, r.false_pos);
		return 0;
	}
	double best = 1e30;
	for (int rep = 0; rep < REPS; rep++) {
		double t0 = now();
		Result x = once();
		double t = now() - t0;
		if (x.hash != plain.hash) return fprintf(stderr, "nondeterministic\n"), 1;
		if (t < best) best = t;
	}
	if (time_only)
		printf("%.3f\n", best * 1e9 / N);
	else
		printf("%.2f ns/record   %zu block bytes   hash %016llx   bloom false positives %d of %d\n",
		       best * 1e9 / N, r.block, (unsigned long long) r.hash, r.false_pos, N);
	return 0;
}
