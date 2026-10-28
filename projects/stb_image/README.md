# stb_image testbed

A small real project to check that oqed's wins hold up outside microbenchmarks.
`stb_image.h` and `stb_image_write.h` are vendored unchanged from
[nothings/stb](https://github.com/nothings/stb) @ `003501f` (v2.30, public
domain or MIT, see the end of each file).

```
make bench            # clang, default SIMD build
make bench SIMD=0     # scalar build: the JPEG IDCT goes through stbi__clamp
make bench CC=gcc
make compare          # plain build vs oqed-cc build with oqed.lock; pixels must be identical
```

`oqed.lock` holds one entry: `stbi__high_bit` as `mov eax, 31; lzcnt ecx, edi;
sub eax, ecx` (4.3× inlined latency, found in run `20261027-223021-a0ef`).
oqed-cc proves it against stb's own function on every build, with clang and
with gcc. It changes nothing measurable here: stb only calls it while parsing
BMP headers. The point is the pipeline, on a real project.

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
