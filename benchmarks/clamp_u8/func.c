#include <stdint.h>

uint8_t clamp_u8(int32_t a) {
    if (a < 0) return 0;
    if (a > 255) return 255;
    return (uint8_t)a;
}
