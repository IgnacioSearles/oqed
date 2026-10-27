#include <stdint.h>

uint8_t stbi__compute_y(int32_t r, int32_t g, int32_t b) {
    return (uint8_t)(((r * 77) + (g * 150) + (29 * b)) >> 8);
}
