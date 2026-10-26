#include <stdint.h>

uint32_t isolate_lowest_u32(uint32_t x) {
    return x & -x;
}
