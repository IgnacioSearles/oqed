#include <stdint.h>

uint32_t avg_ceil_u32(uint32_t x, uint32_t y) {
    return (x | y) - ((x ^ y) >> 1);
}
