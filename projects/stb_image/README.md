# stb_image testbed

A small real project to check that oqed's wins hold up outside microbenchmarks.
`stb_image.h` and `stb_image_write.h` are vendored unchanged from
[nothings/stb](https://github.com/nothings/stb) @ `003501f` (v2.30, public
domain or MIT, see the end of each file).

```
make bench            # clang, default SIMD build
make bench SIMD=0     # scalar build: the JPEG IDCT goes through stbi__clamp
make bench CC=gcc
```

`gen` writes a deterministic corpus (4 PNGs, 4 JPEGs, 1024×768, photo-like
gradients plus noise). `stb_bench` decodes each file 20 times, reports the best
time, and prints an FNV-1a hash of all pixels so two builds can be checked for
identical output.

The six functions oqed searches on are copied into `benchmarks/stbi_*`.

## Where the time goes (laptop, Zen 3, clang -O3, perf)

- **PNG**: ~80% is zlib inflate (`stbi__parse_huffman_block`,
  `stbi__zhuffman_decode`, `stbi__zget8`, `stbi__fill_bits`), which does memory
  accesses in loops and is out of scope for the verifier. Unfiltering, Paeth
  included, is ≤10%. The Paeth loop is latency-bound: each output byte feeds
  the predictor `filter_bytes` positions later.
- **JPEG, scalar build**: `stbi__YCbCr_to_RGB_row` 37%, `stbi__idct_block` 22%,
  `stbi__clamp` 15% (measured with `-fno-inline`, which inflates call-heavy
  functions).
