#include <stdint.h>

int32_t abs32(int32_t x) {
    int32_t m = x >> 31;
    return (x ^ m) - m;
}
