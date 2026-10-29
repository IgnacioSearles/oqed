/* Does a lock found on bench.c's workload speed up workloads oqed never saw?
 *
 * Each workload times get_packed_size + pack (or unpack) per message, best of
 * REPS, on data the search never ran:
 *
 *   mixed    bench.c's telemetry batch, regenerated here: the reference
 *   small    telemetry with small numbers everywhere (ids, offsets, counters),
 *            where the compiler's varint branches predict well
 *   large    telemetry with full-width numbers everywhere
 *   cover    random `Cover` messages: every field type and label, strings,
 *            bytes, nesting, a oneof (another schema entirely)
 *   unpack   unpacking the mixed batch: oqed replaced no unpack code, so this
 *            is the control (it should come out at 1.00x)
 *
 *   pbc_transfer <workload>   one number, ns per message
 *   pbc_transfer --check      a hash of every byte each workload packs and
 *                             unpacks, so two builds can be compared
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "cover.pb-c.h"
#include "telemetry.pb-c.h"

#define N_POINTS 4096
#define N_COVER 400
#define REPS 200

static uint64_t rng;
static uint64_t next(void)
{
	rng ^= rng << 13;
	rng ^= rng >> 7;
	rng ^= rng << 17;
	return rng;
}

static double now(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec * 1e-9;
}

static uint64_t fnv(uint64_t h, const uint8_t *p, size_t n)
{
	for (size_t i = 0; i < n; i++)
		h = (h ^ p[i]) * 0x100000001b3u;
	return h;
}

static Telemetry__Point points[N_POINTS];
static Telemetry__Point *ptrs[N_POINTS];
static Telemetry__Batch batch = TELEMETRY__BATCH__INIT;

static int64_t mixed_value(void)
{
	uint64_t r = next();
	switch (r % 10) {
	case 0: case 1: case 2: case 3: return (int64_t) (r >> 8 & 0x7f);
	case 4: case 5: case 6: return (int64_t) (r >> 8 & 0xfffff);
	case 7: case 8: return (int64_t) (r >> 8 & 0xffffffffffu);
	default: return -(int64_t) (r >> 8 & 0xffff);
	}
}

/* kind: 0 mixed (as bench.c), 1 small, 2 large */
static void make_batch(int kind)
{
	static int64_t last[512];
	memset(last, 0, sizeof last);
	rng = 0x2545f4914f6cdd1du + (uint64_t) kind;
	for (size_t i = 0; i < N_POINTS; i++) {
		Telemetry__Point *p = &points[i];
		unsigned s = (unsigned) (next() % 512);
		telemetry__point__init(p);
		if (kind == 1) {
			p->timestamp_us = i % 100;
			p->series_id = s % 100;
			p->value = (int64_t) (next() % 100);
			p->flags = 0;
		} else if (kind == 2) {
			p->timestamp_us = next() | 1ull << 63;
			p->series_id = next() | 1ull << 63;
			p->value = (int64_t) (next() | 1ull << 62);
			p->flags = (uint32_t) next() | 1ull << 31;
		} else {
			p->timestamp_us = 1761600000000000u + i * 250 + next() % 250;
			p->series_id = next() * 0xff51afd7ed558ccdu;
			p->value = mixed_value();
			p->flags = (next() % 8 == 0) ? (uint32_t) (next() % 4096) : 0;
		}
		p->delta = p->value - last[s];
		last[s] = p->value;
		ptrs[i] = p;
	}
	batch.batch_id = 42;
	batch.n_points = N_POINTS;
	batch.points = ptrs;
}

static uint8_t buf[1 << 24];

/* ns per message to size and pack the batch (`hash`: of the packed bytes). */
static double pack_batch(int reps, uint64_t *hash)
{
	double best = 1e9;
	size_t n = 0;
	for (int r = 0; r < reps; r++) {
		double a = now();
		size_t size = protobuf_c_message_get_packed_size(&batch.base);
		n = protobuf_c_message_pack(&batch.base, buf);
		double b = now();
		if (n != size) {
			fprintf(stderr, "packed %zu, size said %zu\n", n, size);
			exit(1);
		}
		if (b - a < best) best = b - a;
	}
	*hash = fnv(0xcbf29ce484222325u, buf, n);
	return best * 1e9 / N_POINTS;
}

static double unpack_batch(int reps, uint64_t *hash)
{
	size_t n = protobuf_c_message_pack(&batch.base, buf);
	double best = 1e9;
	uint64_t h = 0xcbf29ce484222325u;
	for (int r = 0; r < reps; r++) {
		double a = now();
		Telemetry__Batch *back =
			(Telemetry__Batch *) protobuf_c_message_unpack(&telemetry__batch__descriptor, NULL, n, buf);
		double b = now();
		if (!back || back->n_points != N_POINTS) {
			fprintf(stderr, "unpack failed\n");
			exit(1);
		}
		if (r == 0)
			for (size_t i = 0; i < N_POINTS; i++)
				h = fnv(h, (const uint8_t *) &back->points[i]->timestamp_us, 5 * 8);
		protobuf_c_message_free_unpacked(&back->base, NULL);
		if (b - a < best) best = b - a;
	}
	*hash = h;
	return best * 1e9 / N_POINTS;
}

static Cover covers[N_COVER];

static double pack_cover(int reps, uint64_t *hash)
{
	static int made;
	if (!made) {
		/* The arena is never reset: all N_COVER messages stay alive. */
		cover__seed(1234);
		for (int i = 0; i < N_COVER; i++) cover__random(&covers[i], 0);
		made = 1;
	}
	double best = 1e9;
	uint64_t h = 0;
	for (int r = 0; r < reps; r++) {
		h = 0xcbf29ce484222325u;
		double a = now();
		size_t off = 0;
		for (int i = 0; i < N_COVER; i++) {
			size_t size = protobuf_c_message_get_packed_size(&covers[i].base);
			if (off + size > sizeof buf) off = 0;
			off += protobuf_c_message_pack(&covers[i].base, buf + off);
		}
		double b = now();
		if (r == 0) h = fnv(h, buf, off);
		if (b - a < best) best = b - a;
	}
	*hash = h;
	return best * 1e9 / N_COVER;
}

static double run(const char *w, int reps, uint64_t *hash)
{
	if (!strcmp(w, "mixed")) return make_batch(0), pack_batch(reps, hash);
	if (!strcmp(w, "small")) return make_batch(1), pack_batch(reps, hash);
	if (!strcmp(w, "large")) return make_batch(2), pack_batch(reps, hash);
	if (!strcmp(w, "unpack")) return make_batch(0), unpack_batch(reps / 4 + 1, hash);
	if (!strcmp(w, "cover")) return pack_cover(reps / 4 + 1, hash);
	fprintf(stderr, "unknown workload %s\n", w);
	exit(2);
}

int main(int argc, char **argv)
{
	static const char *all[] = {"mixed", "small", "large", "cover", "unpack"};
	uint64_t h;
	if (argc < 2) return fprintf(stderr, "usage: pbc_transfer <workload>|--check\n"), 2;
	if (!strcmp(argv[1], "--check")) {
		for (size_t i = 0; i < sizeof all / sizeof *all; i++) {
			run(all[i], 1, &h);
			printf("%-7s %016llx\n", all[i], (unsigned long long) h);
		}
		return 0;
	}
	printf("%.3f\n", run(argv[1], REPS, &h));
	return 0;
}
