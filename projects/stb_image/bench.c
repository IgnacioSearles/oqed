// Decodes every file in the corpus, prints an FNV-1a hash of the pixels (so
// builds can be checked for identical output) and the best-of-N decode time
// per format.
#define STB_IMAGE_IMPLEMENTATION
#include "stb_image.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}

static unsigned char *slurp(const char *path, int *len) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    *len = (int)ftell(f);
    rewind(f);
    unsigned char *buf = malloc(*len);
    if (fread(buf, 1, *len, f) != (size_t)*len) exit(1);
    fclose(f);
    return buf;
}

int main(int argc, char **argv) {
    int reps = 20;
    double total[2] = {0, 0};
    uint64_t hash = 1469598103934665603ull;
    for (int i = 1; i < argc; i++) {
        if (strncmp(argv[i], "--reps=", 7) == 0) { reps = atoi(argv[i] + 7); continue; }
        int len, w, h, n;
        unsigned char *buf = slurp(argv[i], &len);
        if (!buf) { fprintf(stderr, "cannot read %s\n", argv[i]); return 1; }
        double best = 1e9;
        for (int r = 0; r < reps; r++) {
            double t0 = now();
            unsigned char *px = stbi_load_from_memory(buf, len, &w, &h, &n, 0);
            double dt = now() - t0;
            if (!px) { fprintf(stderr, "%s: %s\n", argv[i], stbi_failure_reason()); return 1; }
            if (dt < best) best = dt;
            if (r == 0)
                for (size_t k = 0; k < (size_t)w * h * n; k++) hash = (hash ^ px[k]) * 1099511628211ull;
            stbi_image_free(px);
        }
        int is_png = strstr(argv[i], ".png") != NULL;
        total[is_png] += best;
        printf("%-20s %5dx%-5d %d ch  %8.3f ms\n", argv[i], w, h, n, best * 1e3);
        free(buf);
    }
    printf("jpeg total %.3f ms   png total %.3f ms   pixels fnv1a %016llx\n", total[0] * 1e3, total[1] * 1e3,
           (unsigned long long)hash);
    return 0;
}
