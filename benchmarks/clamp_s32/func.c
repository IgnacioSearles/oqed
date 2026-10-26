#include <stdint.h>

int32_t clamp_s32(int32_t a, int32_t lo, int32_t hi) {
    if (a < lo) return lo;
    if (a > hi) return hi;
    return a;
}
