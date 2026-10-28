/* Serialize a batch of telemetry points with protobuf-c.
 *
 * The batch is deterministic: 4096 points from a few hundred series, with
 * value magnitudes mixed the way metrics are (small counters, larger gauges,
 * byte counts, some negative readings). Reports the best time per point for
 * get_packed_size alone and for get_packed_size + pack (what a sender does),
 * and an FNV-1a hash of the packed bytes so two builds can be compared. The
 * packed batch is unpacked once and checked against the input.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

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

static double now(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec * 1e-9;
}

int main(void)
{
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
	for (int r = 0; r < REPS; r++) {
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
	printf("get_packed_size %6.2f ns/point   size+pack %6.2f ns/point   %zu bytes   hash %016llx\n",
	       best_size * 1e9 / N_POINTS, best_pack * 1e9 / N_POINTS, size, (unsigned long long) h);
	free(buf);
	return 0;
}
