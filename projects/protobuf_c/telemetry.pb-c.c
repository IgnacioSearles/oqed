/* Hand-written in the form protoc-c generates for telemetry.proto. */
#include <stddef.h>

#include "telemetry.pb-c.h"

void telemetry__point__init(Telemetry__Point *message)
{
	static const Telemetry__Point init_value = TELEMETRY__POINT__INIT;
	*message = init_value;
}

void telemetry__batch__init(Telemetry__Batch *message)
{
	static const Telemetry__Batch init_value = TELEMETRY__BATCH__INIT;
	*message = init_value;
}

static const ProtobufCFieldDescriptor telemetry__point__field_descriptors[5] = {
	{ "timestamp_us", 1, PROTOBUF_C_LABEL_NONE, PROTOBUF_C_TYPE_UINT64, 0,
	  offsetof(Telemetry__Point, timestamp_us), NULL, NULL, 0, 0, NULL, NULL },
	{ "series_id", 2, PROTOBUF_C_LABEL_NONE, PROTOBUF_C_TYPE_UINT64, 0,
	  offsetof(Telemetry__Point, series_id), NULL, NULL, 0, 0, NULL, NULL },
	{ "value", 3, PROTOBUF_C_LABEL_NONE, PROTOBUF_C_TYPE_INT64, 0,
	  offsetof(Telemetry__Point, value), NULL, NULL, 0, 0, NULL, NULL },
	{ "delta", 4, PROTOBUF_C_LABEL_NONE, PROTOBUF_C_TYPE_SINT64, 0,
	  offsetof(Telemetry__Point, delta), NULL, NULL, 0, 0, NULL, NULL },
	{ "flags", 5, PROTOBUF_C_LABEL_NONE, PROTOBUF_C_TYPE_UINT32, 0,
	  offsetof(Telemetry__Point, flags), NULL, NULL, 0, 0, NULL, NULL },
};
static const unsigned telemetry__point__field_indices_by_name[] = { 3, 4, 1, 0, 2 };
static const ProtobufCIntRange telemetry__point__number_ranges[1 + 1] = { { 1, 0 }, { 0, 5 } };
const ProtobufCMessageDescriptor telemetry__point__descriptor = {
	PROTOBUF_C__MESSAGE_DESCRIPTOR_MAGIC,
	"telemetry.Point",
	"Point",
	"Telemetry__Point",
	"telemetry",
	sizeof(Telemetry__Point),
	5,
	telemetry__point__field_descriptors,
	telemetry__point__field_indices_by_name,
	1, telemetry__point__number_ranges,
	(ProtobufCMessageInit) telemetry__point__init,
	NULL, NULL, NULL
};

static const ProtobufCFieldDescriptor telemetry__batch__field_descriptors[2] = {
	{ "batch_id", 1, PROTOBUF_C_LABEL_NONE, PROTOBUF_C_TYPE_UINT64, 0,
	  offsetof(Telemetry__Batch, batch_id), NULL, NULL, 0, 0, NULL, NULL },
	{ "points", 2, PROTOBUF_C_LABEL_REPEATED, PROTOBUF_C_TYPE_MESSAGE,
	  offsetof(Telemetry__Batch, n_points), offsetof(Telemetry__Batch, points),
	  &telemetry__point__descriptor, NULL, 0, 0, NULL, NULL },
};
static const unsigned telemetry__batch__field_indices_by_name[] = { 0, 1 };
static const ProtobufCIntRange telemetry__batch__number_ranges[1 + 1] = { { 1, 0 }, { 0, 2 } };
const ProtobufCMessageDescriptor telemetry__batch__descriptor = {
	PROTOBUF_C__MESSAGE_DESCRIPTOR_MAGIC,
	"telemetry.Batch",
	"Batch",
	"Telemetry__Batch",
	"telemetry",
	sizeof(Telemetry__Batch),
	2,
	telemetry__batch__field_descriptors,
	telemetry__batch__field_indices_by_name,
	1, telemetry__batch__number_ranges,
	(ProtobufCMessageInit) telemetry__batch__init,
	NULL, NULL, NULL
};
