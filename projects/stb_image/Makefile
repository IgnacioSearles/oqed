# Small real-world testbed for oqed: stb_image decoding a generated corpus.
#   make bench              build and run (default SIMD build)
#   make bench SIMD=0       scalar build (STBI_NO_SIMD): the JPEG IDCT uses stbi__clamp
#   make bench CC=gcc
ifeq ($(origin CC),default)
CC = clang
endif
SIMD ?= 1
CFLAGS ?= -O3 -march=x86-64-v3
ifeq ($(SIMD),0)
CFLAGS += -DSTBI_NO_SIMD
endif

CORPUS = $(foreach i,0 1 2 3,corpus/img$(i).png corpus/img$(i).jpg)

bench: stb_bench $(CORPUS)
	./stb_bench $(CORPUS)

stb_bench: bench.c stb_image.h
	$(CC) $(CFLAGS) -o $@ bench.c -lm

$(CORPUS) &: gen.c stb_image_write.h
	mkdir -p corpus
	$(CC) -O2 -o gen gen.c -lm && ./gen

clean:
	rm -rf stb_bench gen corpus

.PHONY: bench clean
