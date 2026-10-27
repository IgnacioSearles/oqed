// Writes a deterministic test corpus: photo-like RGB/RGBA images (smooth
// gradients, soft edges, sensor-like noise) as PNG and JPEG. The PNG writer
// picks a filter per row, and on images like these it mostly picks Paeth.
#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "stb_image_write.h"
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

static uint32_t rng = 12345;
static int noise(int amp) {
    rng = rng * 1664525u + 1013904223u;
    return (int)(rng >> 24) % (2 * amp + 1) - amp;
}

static unsigned char px(double v) { return v < 0 ? 0 : v > 255 ? 255 : (unsigned char)v; }

static void image(unsigned char *p, int w, int h, int comp, int seed) {
    double cx = w * (0.3 + 0.1 * seed), cy = h * 0.6;
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            double r = hypot(x - cx, y - cy) / w;
            double blob = 1 / (1 + exp((r - 0.25) * 40));
            double sky = (double)y / h;
            unsigned char *o = p + (y * w + x) * comp;
            o[0] = px(60 + 120 * sky + 70 * blob + 10 * sin(x * 0.05 + seed) + noise(3));
            o[1] = px(90 + 80 * sky + 30 * blob + 10 * cos(y * 0.04) + noise(3));
            o[2] = px(150 - 60 * sky + 20 * blob + noise(3));
            if (comp == 4) o[3] = px(255 - 80 * blob + noise(1));
        }
}

int main(void) {
    const int w = 1024, h = 768;
    unsigned char *p = malloc(w * h * 4);
    char name[64];
    for (int i = 0; i < 4; i++) {
        int comp = i % 2 ? 4 : 3;
        image(p, w, h, comp, i);
        snprintf(name, sizeof name, "corpus/img%d.png", i);
        if (!stbi_write_png(name, w, h, comp, p, w * comp)) return 1;
        image(p, w, h, 3, i);
        snprintf(name, sizeof name, "corpus/img%d.jpg", i);
        if (!stbi_write_jpg(name, w, h, 3, p, 90)) return 1;
    }
    free(p);
    return 0;
}
