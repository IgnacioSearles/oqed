# Trick library: does showing past wins help?

`oqed tricks` collects every verified win in the run logs into `tricks.jsonl`
(24 at the time: the C, the asm, the one-line idea, proven or tested, the
speedup). With `--tricks 3` each function's prompt shows the 3 most similar
ones (TF-IDF over the C's tokens, never the function's own).

**Setup** (`run.sh`): 12 benchmark functions searched without (`--tricks 0`)
and with (`--tricks 3`) the library (`library.jsonl`, frozen copy), twice
each, contributor Muse Spark for both tiers, `--max-usd 3` per run. The same
functions are not in the library, but some near relatives are (`clz_smear`,
`floor_log2_smear` next to `ceil_log2_u32` and `stbi_high_bit`). The four run
logs are in `runs/`; `uv run python experiments/tricks/analyze.py` prints the
table.

| function | no tricks: won, best, first win at candidate | 3 tricks: won, best, first win at candidate |
|---|---|---|
| abs32 | 0/2 | 2/2, 1.05x, 9/5 |
| avg_ceil_u32 | 2/2, 1.33x, 1/2 | 2/2, 1.33x, 4/1 |
| bitreverse_u32 | 0/2 | 0/2 |
| ceil_log2_u32 | 0/2 | 0/2 |
| clamp_u8 | 0/2 | 0/2 |
| ctz_u32 | 1/2, 1.03x, 5 | 0/2 |
| gray_decode_u32 | 0/2 | 0/2 |
| is_pow2_u32 | 0/2 | 0/2 |
| isolate_lowest_u32 | 0/2 | 0/2 |
| popcount_swar_u32 | 0/2 | 0/2 |
| sat_add_s32 | 2/2, 1.67x, 1/1 | 2/2, 1.67x, 1/1 |
| stbi_high_bit | 2/2, 4.50x, 1/1 | 2/2, 4.57x, 1/1 |

| | no tricks | 3 tricks |
|---|---|---|
| function searches won (all proven) | 7 of 24 | 8 of 24 |
| candidates | 229 | 234 |
| spend | $0.46 | $0.48 |

**Result: no measurable effect.** The difference is one marginal win each way
(`abs32` at 1.05x with tricks, `ctz_u32` at 1.03x without), within the
run-to-run noise of two repeats. The `abs32` prompts showed `parity_u32`,
`clz_smear_u32` and `floor_log2_smear_u32`, nothing about absolute values, so
the extra win isn't the library's content. Functions the model already wins
on the first candidate (lzcnt for `stbi_high_bit`, the cmov saturate) gain
nothing to speed up, and those it never wins don't get unstuck.

Why, as far as this shows: the library is small and almost all bit tricks
from the same benchmark set, so the examples repeat what the model already
knows; lexical similarity picks examples that share operators, not ideas.
Places it could still pay off, untested here: a larger library across real
projects (protobuf-c's varint sizing next to another codec's), cheaper models
that know fewer idioms, or retrieval by what the compiler emits rather than
the C. The library stays on by default: it costs a few hundred prompt tokens
and didn't hurt.

Spend for the whole experiment: $2.02, including a first attempt that crashed
on an llvm-mca parse error (fixed in 496a68e) and was re-run.
