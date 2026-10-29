# Muse Spark Open (Baseten) on the held-out functions

`run.sh` searches the 12 functions of the trick-library experiment with
`meta-models/muse-spark-open-1026` on both tiers (`--volume spark-open
--reasoning spark-open`), trick library on, `--max-usd 6`. The comparison is
the two `--tricks 3` runs of contributor Muse Spark 1.3 in
`experiments/tricks` (same functions, same library, same search). Price used
for the cap: $1.25 / $4.25 per million input / output tokens, Baseten's list
rate as reported by third-party price trackers.

## First attempt: no reasoning effort

oqed sent Spark Open no `reasoning_effort` (the model table said the endpoint
didn't take one). On search prompts it then reasons until `max_tokens`
(32k) and never answers: median reply 32,000 tokens, 30 of 56 replies cut
off, $5.86 until the cap stopped the run with 3 functions unsearched, 1 win
(`stbi_high_bit`). One real search prompt, sent four ways:

| setting | time | reply tokens | answered |
|---|---|---|---|
| none | 50 s | 12,000 (the limit) | no |
| low | 11-15 s | 2-3k | yes |
| medium | 16 s | 4k | yes |
| high | 70-117 s | 18-28k | yes, near the limit |

Baseten accepts `reasoning_effort` for it, so oqed now sends it (volume tier
low; `run.sh` sets the reasoning tier to medium). Run log:
`runs/20261029-143349-ab00.jsonl`.

## With reasoning effort

| function | contributor Spark 1.3 (2 runs): won, best | Spark Open (1 run): won, best |
|---|---|---|
| stbi_high_bit | 2/2, 4.57x | 1/1, 4.33x |
| sat_add_s32 | 2/2, 1.67x | 1/1, 1.25x |
| avg_ceil_u32 | 2/2, 1.33x | 0/1 |
| abs32 | 2/2, 1.05x | 0/1 |
| the other 8 | 0/2 | 0/1 |

| | contributor Spark 1.3 | Spark Open |
|---|---|---|
| function searches won (all proven) | 8 of 24 (4 per run) | 2 of 12 |
| candidates per run | 117 | 104 |
| spend per run | $0.24 | $1.83 |
| reply tokens (median) | | 2,400 |

Spark Open finds the missed idioms (lzcnt for `stbi_high_bit`, a cmov
saturate for `sat_add_s32`) but not the second-tier ones contributor Spark
gets (the `stc`/`adc`/`rcr` average, the `abs32` cmov), and its `sat_add_s32`
is slower (1.25x vs 1.67x). It repeats itself more: 23 of 104 candidates were
duplicates. At list price it costs ~8x per run here. One run, so the
per-function differences are small numbers; the direction matches across all
four functions either model won. Run log: `runs/20261029-144810-a322.jsonl`.
