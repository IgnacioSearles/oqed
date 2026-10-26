#include <stdint.h>

int16_t clamp_s16(int32_t a) {
    if (a < -32768) return -32768;
    if (a > 32767) return 32767;
    return (int16_t)a;
}
