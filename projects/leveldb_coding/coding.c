/* LevelDB util/coding.cc (google/leveldb bb74ef7), in C. */
#include "coding.h"

#include <stdlib.h>
#include <string.h>

void PutFixed32(Buf *dst, uint32_t value)
{
	char buf[sizeof(value)];
	EncodeFixed32(buf, value);
	buf_append(dst, buf, sizeof(buf));
}

void PutFixed64(Buf *dst, uint64_t value)
{
	char buf[sizeof(value)];
	EncodeFixed64(buf, value);
	buf_append(dst, buf, sizeof(buf));
}

char *EncodeVarint32(char *dst, uint32_t v)
{
	// Operate on characters as unsigneds
	uint8_t *ptr = (uint8_t *) dst;
	static const int B = 128;
	if (v < (1 << 7)) {
		*(ptr++) = v;
	} else if (v < (1 << 14)) {
		*(ptr++) = v | B;
		*(ptr++) = v >> 7;
	} else if (v < (1 << 21)) {
		*(ptr++) = v | B;
		*(ptr++) = (v >> 7) | B;
		*(ptr++) = v >> 14;
	} else if (v < (1 << 28)) {
		*(ptr++) = v | B;
		*(ptr++) = (v >> 7) | B;
		*(ptr++) = (v >> 14) | B;
		*(ptr++) = v >> 21;
	} else {
		*(ptr++) = v | B;
		*(ptr++) = (v >> 7) | B;
		*(ptr++) = (v >> 14) | B;
		*(ptr++) = (v >> 21) | B;
		*(ptr++) = v >> 28;
	}
	return (char *) ptr;
}

void PutVarint32(Buf *dst, uint32_t v)
{
	char buf[5];
	char *ptr = EncodeVarint32(buf, v);
	buf_append(dst, buf, ptr - buf);
}

char *EncodeVarint64(char *dst, uint64_t v)
{
	static const int B = 128;
	uint8_t *ptr = (uint8_t *) dst;
	while (v >= B) {
		*(ptr++) = v | B;
		v >>= 7;
	}
	*(ptr++) = (uint8_t) v;
	return (char *) ptr;
}

void PutVarint64(Buf *dst, uint64_t v)
{
	char buf[10];
	char *ptr = EncodeVarint64(buf, v);
	buf_append(dst, buf, ptr - buf);
}

void PutLengthPrefixedSlice(Buf *dst, Slice value)
{
	PutVarint32(dst, value.size);
	buf_append(dst, value.data, value.size);
}

int VarintLength(uint64_t v)
{
	int len = 1;
	while (v >= 128) {
		v >>= 7;
		len++;
	}
	return len;
}

const char *GetVarint32PtrFallback(const char *p, const char *limit, uint32_t *value)
{
	uint32_t result = 0;
	for (uint32_t shift = 0; shift <= 28 && p < limit; shift += 7) {
		uint32_t byte = *((const uint8_t *) p);
		p++;
		if (byte & 128) {
			// More bytes are present
			result |= ((byte & 127) << shift);
		} else {
			result |= (byte << shift);
			*value = result;
			return p;
		}
	}
	return NULL;
}

int GetVarint32(Slice *input, uint32_t *value)
{
	const char *p = input->data;
	const char *limit = p + input->size;
	const char *q = GetVarint32Ptr(p, limit, value);
	if (q == NULL) {
		return 0;
	} else {
		*input = (Slice) {q, limit - q};
		return 1;
	}
}

const char *GetVarint64Ptr(const char *p, const char *limit, uint64_t *value)
{
	uint64_t result = 0;
	for (uint32_t shift = 0; shift <= 63 && p < limit; shift += 7) {
		uint64_t byte = *((const uint8_t *) p);
		p++;
		if (byte & 128) {
			// More bytes are present
			result |= ((byte & 127) << shift);
		} else {
			result |= (byte << shift);
			*value = result;
			return p;
		}
	}
	return NULL;
}

int GetVarint64(Slice *input, uint64_t *value)
{
	const char *p = input->data;
	const char *limit = p + input->size;
	const char *q = GetVarint64Ptr(p, limit, value);
	if (q == NULL) {
		return 0;
	} else {
		*input = (Slice) {q, limit - q};
		return 1;
	}
}

int GetLengthPrefixedSlice(Slice *input, Slice *result)
{
	uint32_t len;
	if (GetVarint32(input, &len) && input->size >= len) {
		*result = (Slice) {input->data, len};
		input->data += len;
		input->size -= len;
		return 1;
	} else {
		return 0;
	}
}
