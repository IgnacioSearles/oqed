#include <stdint.h>

int32_t sat_add_s32(int32_t a, int32_t b) {
    int64_t s = (int64_t)a + b;
    if (s > INT32_MAX) return INT32_MAX;
    if (s < INT32_MIN) return INT32_MIN;
    return (int32_t)s;
}
