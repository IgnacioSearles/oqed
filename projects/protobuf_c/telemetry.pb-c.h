/* Hand-written in the form protoc-c generates for telemetry.proto. */
#ifndef TELEMETRY_PB_C_H
#define TELEMETRY_PB_C_H

#include "protobuf-c.h"

typedef struct Telemetry__Point {
	ProtobufCMessage base;
	uint64_t timestamp_us;
	uint64_t series_id;
	int64_t value;
	int64_t delta;
	uint32_t flags;
} Telemetry__Point;
#define TELEMETRY__POINT__INIT \
	{ PROTOBUF_C_MESSAGE_INIT(&telemetry__point__descriptor), 0, 0, 0, 0, 0 }

typedef struct Telemetry__Batch {
	ProtobufCMessage base;
	uint64_t batch_id;
	size_t n_points;
	Telemetry__Point **points;
} Telemetry__Batch;
#define TELEMETRY__BATCH__INIT \
	{ PROTOBUF_C_MESSAGE_INIT(&telemetry__batch__descriptor), 0, 0, NULL }

extern const ProtobufCMessageDescriptor telemetry__point__descriptor;
extern const ProtobufCMessageDescriptor telemetry__batch__descriptor;

void telemetry__point__init(Telemetry__Point *message);
void telemetry__batch__init(Telemetry__Batch *message);

#endif
