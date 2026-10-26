#include <stdint.h>

uint32_t xxh32_avalanche(uint32_t h) {
    h ^= h >> 15;
    h *= 0x85EBCA77u;
    h ^= h >> 13;
    h *= 0xC2B2AE3Du;
    h ^= h >> 16;
    return h;
}
