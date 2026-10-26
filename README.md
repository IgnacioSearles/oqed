# oqed

**Faster than -O3. Proven equivalent.**

oqed is an LLM-guided superoptimizer for x86-64. Given a small hot C function, it searches for assembly that runs faster than both `clang -O3` and `gcc -O3`, then formally proves the result equivalent to the original. Meta's Muse models generate candidate rewrites. A pipeline of increasingly strict checks decides which ones survive: assembly, differential testing, SMT equivalence, then benchmarking. Counterexamples from failed candidates go back to the model.

Built for the Meta Global AI Developer Hackathon (Push the Frontier).

> **Status: early development.** The baseline pipeline, benchmark set, and event log are in place. The search loop, harness, verifier, and UI are not built yet. No speedup or proof claims are made until they are measured and verified.

## How it works

```
 C function ──► clang/gcc -O3 baselines + llvm-mca bottleneck report
                    │
                    ▼
        Muse Spark (volume) / Muse Wonder (reasoning on stalls)
                    │  candidate assembly + one-line explanation
                    ▼
  assemble ─► ISA check ─► differential test ─► SMT proof ─► llvm-mca ─► measured cycles
                    │  counterexamples fed back to the model
                    ▼
        best verified candidates ─► drop-in inline-asm patch
```

- **Models:** both tiers use the [Meta Model API](https://dev.meta.ai/docs/overview). `muse-spark-1.3` generates most candidates in parallel, and `muse-wonder-1.0` is called with the full search history when progress stalls.
- **Correctness first:** "proven equivalent" is claimed only for loop-free code that passes symbolic equivalence checking (angr + Z3). Anything else is labeled "tested, not proven". The verifier will ship with a suite of deliberately wrong rewrites it must reject.
- **Fair comparison:** candidates may only use instructions available to the baseline `-march` (default `x86-64-v3`). This is enforced by decoding the machine code, since assemblers don't check it.
- **Honest numbers:** measurements are reported with variance. Microbenchmark speedups are never presented as whole-program speedups.

## Quick start

Requires Linux x86-64, [uv](https://docs.astral.sh/uv/), clang, gcc, binutils, and `llvm-mca`.

```sh
uv sync
cp .env.example .env              # add your MODEL_API_KEY

uv run oqed baseline round_up_pow2_u32    # clang/gcc output + llvm-mca estimates
uv run oqed scout                         # baseline table for every target
uv run oqed fake-run                      # synthetic run log for UI development
uv run pytest
```

## Layout

```
src/oqed/      orchestrator: targets, toolchain wrappers, ISA check, event schema, CLI
benchmarks/    one directory per target: func.c + meta.toml
tests/         test suite
runs/          JSONL event logs (curated demo runs in runs/demo/)
```

Each `runs/<run_id>.jsonl` file holds one event per line (`baseline_ready`, `candidate_proposed`, `candidate_rejected`, ...). It is the source of truth for the UI and for replays.

## Benchmark set

24 loop-free integer functions:

- bit tricks from *Hacker's Delight* (abs, sign, averages, power-of-two rounding, parity, bit reversal, ...)
- "missed idiom" variants that compilers don't recognize (smear + popcount for clz/log2, portable high multiply)
- clamp and saturating-add primitives modeled on FFmpeg's `av_clip*` / `av_sat_add32`
- hash finalizers (MurmurHash3 `fmix32`/`fmix64`, xxHash avalanche)

## Credits

- Bit-manipulation targets follow algorithms from Henry S. Warren Jr., *Hacker's Delight* (2nd ed.). The implementations here are our own.
- `fmix32` / `fmix64` are from MurmurHash3 by Austin Appleby (public domain).
- xxHash avalanche constants come from xxHash by Yann Collet (BSD 2-Clause). The finalizer implementations here are our own.
- Built on [angr](https://angr.io), [Z3](https://github.com/Z3Prover/z3), [iced-x86](https://github.com/icedland/iced), and LLVM's `llvm-mca`.
