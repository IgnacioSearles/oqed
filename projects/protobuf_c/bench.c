/* Serialize a batch of telemetry points with protobuf-c.
 *
 * The batch is deterministic: 4096 points from a few hundred series, with
 * value magnitudes mixed the way metrics are (small counters, larger gauges,
 * byte counts, some negative readings). Reports the best time per point for
 * get_packed_size alone and for get_packed_size + pack (what a sender does),
 * and an FNV-1a hash of the packed bytes so two builds can be compared. The
 * packed batch is unpacked once and checked against the input.
 *
 *   pbc_bench           both timings, size and hash
 *   pbc_bench --check   deterministic output only (size, hash, round trip), then the
 *                       coverage check below: oqed's test oracle
 *   pbc_bench --time    one number, size+pack ns/point: oqed's bench command
 */
#include <string.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#include "cover.pb-c.h"
#include "telemetry.pb-c.h"

#define N_POINTS 4096
#define REPS 200

static uint64_t rng = 0x9e3779b97f4a7c15u;
static uint64_t next(void)
{
	rng ^= rng << 13;
	rng ^= rng >> 7;
	rng ^= rng << 17;
	return rng;
}

static int64_t metric_value(void)
{
	uint64_t r = next();
	switch (r % 10) {
	case 0: case 1: case 2: case 3:
		return (int64_t) (r >> 8 & 0x7f); /* small counters, status codes */
	case 4: case 5: case 6:
		return (int64_t) (r >> 8 & 0xfffff); /* latencies in us, queue depths */
	case 7: case 8:
		return (int64_t) (r >> 8 & 0xffffffffffu); /* byte counts */
	default:
		return -(int64_t) (r >> 8 & 0xffff); /* negative gauges */
	}
}

/* The coverage check: random `Cover` messages (every field type and label,
 * edge values, nesting, a oneof, large field numbers) through every packing
 * entry point and back. Prints hashes of everything packed, so a build that
 * packs any byte differently prints a different line; exits nonzero on an
 * inconsistency a single build can see by itself. */
#define N_COVER 500

static uint64_t fnv(uint64_t h, const uint8_t *p, size_t n)
{
	for (size_t i = 0; i < n; i++)
		h = (h ^ p[i]) * 0x100000001b3u;
	return h;
}

static int cover_check(void)
{
	static uint8_t buf[1 << 22], again[1 << 22];
	uint64_t h_pack = 0xcbf29ce484222325u, h_lite = h_pack;
	size_t total = 0;
	cover__seed(7);
	for (int i = 0; i < N_COVER; i++) {
		cover__arena_reset();
		Cover m;
		cover__random(&m, 0);
		size_t size = protobuf_c_message_get_packed_size(&m.base);
		if (size > sizeof buf) return fprintf(stderr, "cover %d: too big\n", i), 1;
		size_t n = protobuf_c_message_pack(&m.base, buf);
		if (n != size) return fprintf(stderr, "cover %d: packed %zu, size said %zu\n", i, n, size), 1;
		ProtobufCBufferSimple simple = PROTOBUF_C_BUFFER_SIMPLE_INIT(again);
		size_t n2 = protobuf_c_message_pack_to_buffer(&m.base, &simple.base);
		if (n2 != n || simple.len != n || memcmp(simple.data, buf, n))
			return fprintf(stderr, "cover %d: pack_to_buffer differs\n", i), 1;
		PROTOBUF_C_BUFFER_SIMPLE_CLEAR(&simple);
		h_pack = fnv(h_pack, buf, n);
		total += n;

		/* Unpack and pack again: the same bytes. */
		ProtobufCMessage *back = protobuf_c_message_unpack(&cover__descriptor, NULL, n, buf);
		if (!back) return fprintf(stderr, "cover %d: unpack failed\n", i), 1;
		if (protobuf_c_message_get_packed_size(back) != n || protobuf_c_message_pack(back, again) != n ||
		    memcmp(again, buf, n))
			return fprintf(stderr, "cover %d: repack differs\n", i), 1;
		protobuf_c_message_free_unpacked(back, NULL);

		/* Unpack with a third of the fields unknown, and pack that. */
		ProtobufCMessage *lite = protobuf_c_message_unpack(&cover_lite__descriptor, NULL, n, buf);
		if (!lite) return fprintf(stderr, "cover %d: lite unpack failed\n", i), 1;
		size_t ls = protobuf_c_message_get_packed_size(lite), ln = protobuf_c_message_pack(lite, again);
		if (ls != ln) return fprintf(stderr, "cover %d: lite packed %zu, size said %zu\n", i, ln, ls), 1;
		ProtobufCBufferSimple lsimple = PROTOBUF_C_BUFFER_SIMPLE_INIT(buf);
		if (protobuf_c_message_pack_to_buffer(lite, &lsimple.base) != ln || memcmp(lsimple.data, again, ln))
			return fprintf(stderr, "cover %d: lite pack_to_buffer differs\n", i), 1;
		PROTOBUF_C_BUFFER_SIMPLE_CLEAR(&lsimple);
		h_lite = fnv(h_lite, again, ln);
		protobuf_c_message_free_unpacked(lite, NULL);
	}
	printf("cover: %d messages, %zu bytes, hash %016llx, with unknown fields %016llx\n", N_COVER, total,
	       (unsigned long long) h_pack, (unsigned long long) h_lite);
	return 0;
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
	static Telemetry__Point points[N_POINTS];
	static Telemetry__Point *ptrs[N_POINTS];
	static int64_t last[512];
	Telemetry__Batch batch = TELEMETRY__BATCH__INIT;
	uint64_t t0 = 1761600000000000u; /* microseconds since the epoch */

	for (size_t i = 0; i < N_POINTS; i++) {
		Telemetry__Point *p = &points[i];
		unsigned s = (unsigned) (next() % 512);
		telemetry__point__init(p);
		p->timestamp_us = t0 + i * 250 + next() % 250;
		p->series_id = next() * 0xff51afd7ed558ccdu;
		p->value = metric_value();
		p->delta = p->value - last[s];
		last[s] = p->value;
		p->flags = (next() % 8 == 0) ? (uint32_t) (next() % 4096) : 0;
		ptrs[i] = p;
	}
	batch.batch_id = 42;
	batch.n_points = N_POINTS;
	batch.points = ptrs;

	size_t size = protobuf_c_message_get_packed_size(&batch.base);
	uint8_t *buf = malloc(size);
	double best_size = 1e9, best_pack = 1e9;
	volatile size_t sink = 0;
	for (int r = 0; r < (check ? 1 : REPS); r++) {
		double a = now();
		sink += protobuf_c_message_get_packed_size(&batch.base);
		double b = now();
		size_t n = protobuf_c_message_get_packed_size(&batch.base);
		sink += protobuf_c_message_pack(&batch.base, buf) + n;
		double c = now();
		if (b - a < best_size) best_size = b - a;
		if (c - b < best_pack) best_pack = c - b;
	}

	Telemetry__Batch *back = (Telemetry__Batch *)
		protobuf_c_message_unpack(&telemetry__batch__descriptor, NULL, size, buf);
	if (!back || back->n_points != N_POINTS) {
		fprintf(stderr, "round trip failed\n");
		return 1;
	}
	for (size_t i = 0; i < N_POINTS; i++) {
		const Telemetry__Point *p = back->points[i], *q = &points[i];
		if (p->timestamp_us != q->timestamp_us || p->series_id != q->series_id ||
		    p->value != q->value || p->delta != q->delta || p->flags != q->flags) {
			fprintf(stderr, "round trip mismatch at point %zu\n", i);
			return 1;
		}
	}
	protobuf_c_message_free_unpacked(&back->base, NULL);

	uint64_t h = 0xcbf29ce484222325u;
	for (size_t i = 0; i < size; i++)
		h = (h ^ buf[i]) * 0x100000001b3u;
	if (check) {
		printf("%zu bytes   hash %016llx   round trip ok\n", size, (unsigned long long) h);
		free(buf);
		return cover_check();
	}
	else if (time_only)
		printf("%.3f\n", best_pack * 1e9 / N_POINTS);
	else
		printf("get_packed_size %6.2f ns/point   size+pack %6.2f ns/point   %zu bytes   hash %016llx\n",
		       best_size * 1e9 / N_POINTS, best_pack * 1e9 / N_POINTS, size, (unsigned long long) h);
	free(buf);
	return 0;
}
