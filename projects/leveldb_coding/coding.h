/* LevelDB's util/coding.h, util/hash.h and util/bloom.cc interfaces, in C
 * (google/leveldb bb74ef7; BSD license in LICENSE). Bodies are the C++ ones
 * with casts and namespaces removed. */
#ifndef LEVELDB_CODING_H
#define LEVELDB_CODING_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
	const char *data;
	size_t size;
} Slice;

#include "oqed_support/buf.h"

void PutFixed32(Buf *dst, uint32_t value);
void PutFixed64(Buf *dst, uint64_t value);
void PutVarint32(Buf *dst, uint32_t value);
void PutVarint64(Buf *dst, uint64_t value);
void PutLengthPrefixedSlice(Buf *dst, Slice value);
int GetVarint32(Slice *input, uint32_t *value);
int GetVarint64(Slice *input, uint64_t *value);
int GetLengthPrefixedSlice(Slice *input, Slice *result);
const char *GetVarint32PtrFallback(const char *p, const char *limit, uint32_t *value);
const char *GetVarint64Ptr(const char *p, const char *limit, uint64_t *value);
int VarintLength(uint64_t v);
char *EncodeVarint32(char *dst, uint32_t value);
char *EncodeVarint64(char *dst, uint64_t value);

static inline void EncodeFixed32(char *dst, uint32_t value)
{
	uint8_t *const buffer = (uint8_t *) dst;
	buffer[0] = (uint8_t) value;
	buffer[1] = (uint8_t) (value >> 8);
	buffer[2] = (uint8_t) (value >> 16);
	buffer[3] = (uint8_t) (value >> 24);
}

static inline void EncodeFixed64(char *dst, uint64_t value)
{
	uint8_t *const buffer = (uint8_t *) dst;
	for (int i = 0; i < 8; i++) buffer[i] = (uint8_t) (value >> (8 * i));
}

static inline uint32_t DecodeFixed32(const char *ptr)
{
	const uint8_t *const buffer = (const uint8_t *) ptr;
	return ((uint32_t) buffer[0]) | ((uint32_t) buffer[1] << 8) | ((uint32_t) buffer[2] << 16) |
	       ((uint32_t) buffer[3] << 24);
}

static inline uint64_t DecodeFixed64(const char *ptr)
{
	uint64_t lo = DecodeFixed32(ptr), hi = DecodeFixed32(ptr + 4);
	return (hi << 32) | lo;
}

static inline const char *GetVarint32Ptr(const char *p, const char *limit, uint32_t *value)
{
	if (p < limit) {
		uint32_t result = *((const uint8_t *) p);
		if ((result & 128) == 0) {
			*value = result;
			return p + 1;
		}
	}
	return GetVarint32PtrFallback(p, limit, value);
}

uint32_t Hash(const char *data, size_t n, uint32_t seed);

/* BloomFilterPolicy with bits_per_key, k_ as in the constructor. */
typedef struct {
	size_t bits_per_key, k;
} Bloom;

Bloom bloom_policy(int bits_per_key);
void CreateFilter(const Bloom *p, const Slice *keys, int n, Buf *dst);
int KeyMayMatch(const Slice *key, const Slice *bloom_filter);

#endif
