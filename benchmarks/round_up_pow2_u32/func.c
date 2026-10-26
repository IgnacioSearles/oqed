#include <stdint.h>

uint32_t round_up_pow2_u32(uint32_t x) {
    x = x - 1;
    x |= x >> 1;
    x |= x >> 2;
    x |= x >> 4;
    x |= x >> 8;
    x |= x >> 16;
    return x + 1;
}
