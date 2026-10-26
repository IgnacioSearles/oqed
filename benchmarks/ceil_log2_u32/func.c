#include <stdint.h>

int32_t ceil_log2_u32(uint32_t x) {
    if (x <= 1) return 0;
    return 32 - __builtin_clz(x - 1);
}
