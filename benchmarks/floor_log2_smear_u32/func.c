#include <stdint.h>

int32_t floor_log2_smear_u32(uint32_t x) {
    x |= x >> 1;
    x |= x >> 2;
    x |= x >> 4;
    x |= x >> 8;
    x |= x >> 16;
    return __builtin_popcount(x) - 1;
}
