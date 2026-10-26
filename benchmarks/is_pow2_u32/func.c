#include <stdint.h>

int32_t is_pow2_u32(uint32_t x) {
    return x != 0 && (x & (x - 1)) == 0;
}
