#include <stdint.h>

int32_t stbi__high_bit(uint32_t z) {
    int32_t n = 0;
    if (z == 0) return -1;
    if (z >= 0x10000) { n += 16; z >>= 16; }
    if (z >= 0x00100) { n += 8; z >>= 8; }
    if (z >= 0x00010) { n += 4; z >>= 4; }
    if (z >= 0x00004) { n += 2; z >>= 2; }
    if (z >= 0x00002) { n += 1; }
    return n;
}
