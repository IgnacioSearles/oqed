#include <stdint.h>

uint64_t xxh64_avalanche(uint64_t h) {
    h ^= h >> 33;
    h *= 0xC2B2AE3D27D4EB4Full;
    h ^= h >> 29;
    h *= 0x165667B19E3779F9ull;
    h ^= h >> 32;
    return h;
}
