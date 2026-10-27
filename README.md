# oqed

**Faster than -O3. Proven equivalent.**

oqed is an LLM-guided superoptimizer for x86-64. Given a small hot C function, it searches for assembly that runs faster than both `clang -O3` and `gcc -O3`, then formally proves the result equivalent to the original. Meta's Muse models generate candidate rewrites. A pipeline of increasingly strict checks decides which ones survive: assembly, differential testing, SMT equivalence, then benchmarking. Counterexamples from failed candidates go back to the model.

Built for the Meta Global AI Developer Hackathon (Push the Frontier).

> **Status: early development.** The model-guided search, sandboxed Rust harness, differential testing and SMT verifier work end to end. The UI is not built yet. Timings so far come from a development laptop (WSL2) and are not publishable numbers.

## How it works

```
 C function ──► clang/gcc -O3 baselines + llvm-mca bottleneck report
                    │
                    ▼
        Muse Spark (volume) / Muse Wonder (reasoning on stalls)
                    │  candidate assembly + one-line explanation
                    ▼
  assemble ─► ISA check ─► differential test ─► llvm-mca ─► measured latency ─► SMT proof
                    │  counterexamples fed back to the model
                    ▼
        best verified candidates ─► drop-in inline-asm patch
```

- **Models:** `muse-spark-1.3` ([Meta Model API](https://dev.meta.ai/docs/overview)) generates most candidates in parallel, and `muse-wonder-1.0` is called with the full search history when progress stalls. Muse Spark Open (`meta-models/muse-spark-open-1026` on [Baseten](https://docs.baseten.co)) can run the volume tier instead.
- **Correctness first:** "proven equivalent" is claimed only when symbolic execution (angr/VEX) plus Z3 shows the candidate matches the reference for every ABI-valid input: return value at its width, callee-saved registers and stack pointer. Loops, non-stack memory, instructions with undefined results (`bsf`/`bsr` on zero) and solver timeouts give "unknown", shown as **tested, not proven**. SMT counterexamples are replayed on the CPU. `tests/test_verify.py` holds a suite of deliberately wrong rewrites (wrapping shift counts, reading undefined upper register bits, off-by-one, wrong flag, wrong sign extension, clobbered `rbx`, unbalanced stack, a bug on a single input that random testing misses, ...), and none may be proven.
- **Fair comparison:** candidates may only use instructions available to the baseline `-march` (default `x86-64-v3`). This is enforced by decoding the machine code, since assemblers don't check it.
- **Honest numbers:** measurements are reported with variance. Microbenchmark speedups are never presented as whole-program speedups.

## Quick start

Requires Linux x86-64, [uv](https://docs.astral.sh/uv/), a Rust toolchain, clang, gcc, binutils, and `llvm-mca`. `uv sync` builds the Rust harness.

```sh
uv sync
cp .env.example .env              # add MODEL_API_KEY (and BASETEN_API_KEY for Muse Spark Open)

uv run oqed search round_up_pow2_u32      # model-guided search, events in runs/<id>.jsonl
uv run oqed search parity_u32 --volume spark-open
uv run oqed try round_up_pow2_u32 my.s    # check a hand-written candidate
uv run oqed baseline round_up_pow2_u32    # clang/gcc output + llvm-mca estimates
uv run oqed scout                         # baseline table for every target
uv run oqed fake-run                      # synthetic run log for UI development
uv run pytest && (cd harness && cargo test)
```

Set `OQED_BENCH_CPU=<n>` to pin benchmarks to one core.

## Layout

```
src/oqed/      orchestrator: search loop, providers, prompts, testing, toolchain wrappers, ISA check, events, CLI
harness/       Rust (PyO3) extension: runs and times candidates in a forked, sandboxed child
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
