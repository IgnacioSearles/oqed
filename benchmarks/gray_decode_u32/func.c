#include <stdint.h>

uint32_t gray_decode_u32(uint32_t g) {
    g ^= g >> 1;
    g ^= g >> 2;
    g ^= g >> 4;
    g ^= g >> 8;
    g ^= g >> 16;
    return g;
}
