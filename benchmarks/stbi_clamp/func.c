#include <stdint.h>

uint8_t stbi__clamp(int32_t x) {
    if ((uint32_t)x > 255) {
        if (x < 0) return 0;
        if (x > 255) return 255;
    }
    return (uint8_t)x;
}
