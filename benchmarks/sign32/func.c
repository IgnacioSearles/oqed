#include <stdint.h>

int32_t sign32(int32_t x) {
    return (x > 0) - (x < 0);
}
