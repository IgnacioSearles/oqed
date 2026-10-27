#include <stdint.h>

int32_t stbi__paeth(int32_t a, int32_t b, int32_t c) {
    int32_t thresh = c * 3 - (a + b);
    int32_t lo = a < b ? a : b;
    int32_t hi = a < b ? b : a;
    int32_t t0 = (hi <= thresh) ? lo : c;
    int32_t t1 = (thresh <= lo) ? hi : t0;
    return t1;
}
