#include <stdint.h>

uint32_t mulhi_u32_portable(uint32_t u, uint32_t v) {
    uint32_t u0 = u & 0xFFFF, u1 = u >> 16;
    uint32_t v0 = v & 0xFFFF, v1 = v >> 16;
    uint32_t w0 = u0 * v0;
    uint32_t t = u1 * v0 + (w0 >> 16);
    uint32_t w1 = (t & 0xFFFF) + u0 * v1;
    return u1 * v1 + (t >> 16) + (w1 >> 16);
}
