#include <stddef.h>
#include <stdint.h>

static inline size_t uint32_size(uint32_t v) {
	if (v < (1UL << 7)) {
		return 1;
	} else if (v < (1UL << 14)) {
		return 2;
	} else if (v < (1UL << 21)) {
		return 3;
	} else if (v < (1UL << 28)) {
		return 4;
	} else {
		return 5;
	}
}

size_t uint64_size(uint64_t v) {
	uint32_t upper_v = (uint32_t) (v >> 32);

	if (upper_v == 0) {
		return uint32_size((uint32_t) v);
	} else if (upper_v < (1UL << 3)) {
		return 5;
	} else if (upper_v < (1UL << 10)) {
		return 6;
	} else if (upper_v < (1UL << 17)) {
		return 7;
	} else if (upper_v < (1UL << 24)) {
		return 8;
	} else if (upper_v < (1UL << 31)) {
		return 9;
	} else {
		return 10;
	}
}
