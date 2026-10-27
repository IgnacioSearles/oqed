#include <stdint.h>

uint8_t stbi__blinn_8x8(uint8_t x, uint8_t y) {
    uint32_t t = x * y + 128;
    return (uint8_t)((t + (t >> 8)) >> 8);
}
