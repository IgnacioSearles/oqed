#include <stdint.h>

int32_t ctz_u32(uint32_t x) {
    return x ? __builtin_ctz(x) : 32;
}
