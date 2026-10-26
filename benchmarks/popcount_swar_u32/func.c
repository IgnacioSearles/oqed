#include <stdint.h>

int32_t popcount_swar_u32(uint32_t x) {
    x = x - ((x >> 1) & 0x55555555u);
    x = (x & 0x33333333u) + ((x >> 2) & 0x33333333u);
    x = (x + (x >> 4)) & 0x0F0F0F0Fu;
    return (int32_t)((x * 0x01010101u) >> 24);
}
