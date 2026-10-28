#include <stddef.h>
#include <stdint.h>

size_t uint32_size(uint32_t v) {
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
