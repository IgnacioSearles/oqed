# zlib

madler/zlib at 71747d1, configured by `oqed init` (3 attempts, $0.003). The
build compiles `minigzip` with `$CC`; the workload is `minigzip -9` compressing
zlib's own sources concatenated 32 times and decompressing them, twice. The
test requires every file it writes (`oqe_c.gz`, the round trip) to match the C
build's byte for byte.

```
git clone https://github.com/madler/zlib ~/oqed-sweep/zlib && cd ~/oqed-sweep/zlib
git checkout 71747d1
cp ~/oqed/sweep/zlib/oqed.toml .
uv run --project ~/oqed oqed optimize --unsafe --top 6 --candidates 40 --wonder 1 --max-usd 30
uv run --project ~/oqed python ~/oqed/sweep/zlib/compilers.py ~/oqed-sweep/zlib
```

**Scan.** 167 functions, 93 in scope. `longest_match` is 88% of the samples,
then `deflate_slow` 5.8%, `inflate_fast` 2.4%, `compress_block` 1.4%,
`crc32_z` 0.5%.

**Search.** Muse Spark (contributor), with Muse Wonder writing one candidate
of each first wave and taking over on stalls. $15.50 in three runs (one
stopped after $0.14 to add struct layouts to the prompt; one of `deflate_slow`
and `inflate_fast` again with crash reports), plus $0.35 for the first,
shallow run.

| function | candidates | result |
|---|---|---|
| `longest_match` | 24 | **1.17x** (tested only) |
| `crc32_z` | 12 | 1.04x (tested only) |
| `deflate_slow` | 12 + 6 | no win |
| `inflate_fast` | 12 + 12 | no win |
| `compress_block`, `build_tree` | 9 each | no win |

The `longest_match` that won is Muse Wonder's, a refinement of its own earlier
candidate: it loads the next hash-chain entry while the current candidate's
bytes are still being compared, so the chain walk's dependent loads overlap
the match checks, and it keeps all the loop's state in registers. Muse
Spark came close (1.167x, `longest_match-023`); all 12 `longest_match`
candidates that passed the tests timed faster than the C, 1.08x to 1.17x.
`crc32_z` is a single little-endian braid loop.

**Result** (`compilers.json`, both entries tested only, output files
identical, 11 alternating runs each):

| compiler | lock | plain | oqed | whole program |
|---|---|---|---|---|
| clang | both | 3048.6 ms | 2601.8 ms | **1.172x**, 11 of 11 |
| clang | `longest_match` | 3011.2 ms | 2604.3 ms | 1.156x, 11 of 11 |
| gcc | both | 2955.4 ms | 2581.7 ms | **1.145x**, 11 of 11 |
| gcc | `longest_match` | 3082.2 ms | 2692.8 ms | 1.145x, 11 of 11 |

`oqed report` after the search gave 1.168x and 1.166x (clang, 11 of 11).

**The big functions.** Most candidates for `deflate_slow`, `inflate_fast` and
`compress_block` (hundreds of instructions, calls into the rest of zlib)
crashed or wrote a different `.gz`. Two things came out of it: project
prompts now give the layout of every struct the slice defines (field offsets
and sizes, from clang), and a crashing test reports where it crashed:
`SIGSEGV at inflate_fast+0x7c: movzx eax,BYTE PTR [r14] (fault address
0x7e328185)` plus the registers, here a pointer truncated to 32 bits. The
follow-up run on those two functions still found nothing faster.
