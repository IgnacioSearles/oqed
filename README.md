# oqed

**Faster than -O3. Proven equivalent.**

oqed is an LLM-guided superoptimizer for x86-64. Given a small hot C function, it searches for assembly that runs faster than both `clang -O3` and `gcc -O3`, then formally proves the result equivalent to the original. Meta's Muse models generate candidate rewrites. A pipeline of increasingly strict checks decides which ones survive: assembly, differential testing, SMT equivalence, then benchmarking. Counterexamples from failed candidates go back to the model.

Built for the Meta Global AI Developer Hackathon (Push the Frontier).

> **Status: early development.** The model-guided search, sandboxed Rust harness, differential testing, SMT verifier, and the UI's result and search views (with replay) work end to end. The build integration (`oqed optimize` → `oqed.lock` → `oqed-cc`) works on two real projects: on protobuf-c, `oqed optimize` with no hand-picked functions makes `get_packed_size` 1.51× faster end to end with clang and 1.65× with gcc. Timings so far come from a development laptop (WSL2) and are not publishable numbers.

## How it works

```
 C function ──► clang/gcc -O3 baselines + llvm-mca bottleneck report
                    │
                    ▼
        Muse Spark (volume) / Muse Wonder (reasoning on stalls)
                    │  candidate assembly + one-line explanation
                    ▼
  assemble ─► ISA check ─► differential test ─► llvm-mca ─► inlined latency ─► SMT proof
                    │  counterexamples fed back to the model
                    ▼
        best verified candidates ─► oqed.lock ─► make CC=oqed-cc
```

- **Models:** `muse-spark-1.3` ([Meta Model API](https://dev.meta.ai/docs/overview)) generates most candidates in parallel, and `muse-wonder-1.0` is called with the full search history and the best candidate's bottleneck report when progress stalls. Muse Spark Open (`meta-models/muse-spark-open-1026` on [Baseten](https://docs.baseten.co)) can run the volume tier instead.
- **Search:** each volume-tier call mutates one parent, drawn from the fastest candidates that beat `-O3` (or, before any do, the correct ones that came closest), and sees a one-line log of every idea already tried. Duplicates are caught on text and on machine code. A function stops early once the reasoning tier has failed to improve it twice (`--reasoning-attempts`). Every run has a spending cap (`--max-usd`, default $1, list-price estimate from token counts) and logs its model spend.
- **Correctness first:** "proven equivalent" is claimed only when symbolic execution (angr/VEX) plus Z3 shows the candidate matches the reference for every ABI-valid input: return value at its width, callee-saved registers and stack pointer. Loops (cycles in the control flow; a backward jump to a shared tail is fine), non-stack memory, instructions with undefined results (`bsf`/`bsr` on zero) and solver timeouts give "unknown", shown as **tested, not proven**. SMT counterexamples are replayed on the CPU. `tests/test_verify.py` holds a suite of deliberately wrong rewrites (wrapping shift counts, reading undefined upper register bits, off-by-one, wrong flag, wrong sign extension, clobbered `rbx`, unbalanced stack, a bug on a single input that random testing misses, ...), and none may be proven.
- **Fair comparison:** candidates may only use instructions available to the baseline `-march` (default `x86-64-v3`). This is enforced by decoding the machine code, since assemblers don't check it.
- **Honest numbers:** measurements are reported with variance. Microbenchmark speedups are never presented as whole-program speedups.
- **Inlined latency:** the search measures every candidate as an `asm` statement inside a loop where each result feeds the next call, against the C compiled into the same loop by clang and gcc in the same run (`--bench call` restores call/ret timing). Candidates that touch memory or the stack are rejected as not inlinable. `oqed inline-bench <run>` turns each winner into a GNU `asm` statement (argument registers bound by constraint, exact clobbers from the decoded instructions), times it against the C source inside a dependent loop (latency) and an independent loop (throughput, with and without the compiler's auto-vectorization), and re-proves the bytes the compiler emits around the `asm` statement. Timing through `call`/`ret` adds ~4.7 ticks per call and can rank candidates wrongly: on stb_image, call-mode wins of 1.2–1.4× on `paeth` and `blinn_8x8` came out at 1.00× inlined, and `bitreverse16` went from 1.17× to 0.52×. Real missed-idiom wins were understated instead: re-measured inlined, `clz_smear` went from 2.35× to 6.5×, `parity` from 1.74× to 3.5×, `round_up_pow2` from 2.2× to 2.6×, and stb's `high_bit` (lzcnt) is 4.3×. Against a loop the compiler auto-vectorizes, every one of them loses throughput (0.2–0.97×). The loops replay 65,536 shuffled inputs: with the 1,024 used at first, the branch predictor learned the sequence, and protobuf-c's branchy `uint64_size` timed at ~3 ticks instead of the ~12 it costs when its branches miss.
- **Whole projects, no hand-picked functions:** a project's `oqed.toml` names a `build` command and a `run` command (its workload). `oqed scan` builds it through oqed-cc in record mode, which logs every compile and links in a call counter (`-finstrument-functions`, so inlined calls count too), runs the workload, and walks clang's AST of every compiled file for function definitions in the project. Each one oqed can search today (integer arguments and result in registers, leaf code that reads no global data) becomes a target: a slice of the preprocessed source with the typedefs, enums and helpers it uses. `oqed optimize` with no names searches them most-called first under the spending cap. On protobuf-c, `scan` finds 96 functions, 10 in scope, and ranks `get_tag_size` first (6.8M calls in the workload). It also lists the hottest functions oqed can't take yet (pointer arguments, memory), which is where the next work goes.
- **Functions that touch memory:** functions that take pointers, return nothing or read globals can't run alone on random inputs, so the project judges them. Each candidate is built into a copy of the project (oqed-cc in trust mode, the asm as an inline block with a `memory` clobber so callers still inline it), the project's `test` command must print exactly what the C build prints, and its `bench` command is timed against a baseline build, alternating runs. On protobuf-c this took 40 of 96 functions in scope (from 10), and found three varint encoders (`uint32_pack`, `tag_pack`, `uint64_pack`) that make the whole size + pack benchmark 1.22× faster with clang together with the proven entries (1.17× without them). The project's tests rejected 29 wrong candidates on the way.
- **`--unsafe`:** by default only proven winners go into the lock. `oqed optimize --unsafe` also locks winners that could not be proven (status `tested`): a reference with a loop, or a function judged by the project's tests. oqed-cc still re-checks them on every build. A register-only entry is re-proven, a counterexample keeps the C, and an inconclusive proof falls back to differential testing. A project-oracle entry applies only while the function and the declarations it uses hash to what was tested. Either way the substitution is reported as `UNSAFE`.
- **No assembly in your codebase:** `oqed optimize` records proven winners in a reviewable `oqed.lock`, and `oqed-cc` is a drop-in compiler wrapper (`make CC=oqed-cc`, or `CC="oqed-cc --cc=gcc"`) that applies them at build time, with no network calls. For each C file it preprocesses with your flags (so functions in headers are found), locates each lock entry's function with clang's JSON AST, replaces the body with the asm as an inline `asm` statement, and then:
  - **proves the substitution against your function as it is now**, compiled by your compiler with your flags: a wrapper calling the function is compiled from the original and from the rewritten source, and the two are checked equivalent with the SMT verifier. A stale entry, a changed signature, or a `-march` without the instructions the entry needs keeps the C;
  - **keeps the C if any loop stops auto-vectorizing** (compiler vectorization remarks with and without the substitution);
  - reports every decision on stderr, caches it (`~/.cache/oqed/cc`), and on any internal error compiles the original command unchanged.

  `make compare` in a testbed builds with and without `oqed.lock` and checks the output is identical:
  - **`projects/protobuf_c`** (protobuf-c v1.5.1 packing 4096 telemetry points): `oqed optimize --unsafe` found the 10 functions in scope, searched the 6 the workload calls ($0.32 of contributor calls), and locked 4 proven winners: `get_tag_size`, `uint64_size`, `uint32_size` and `sint64_size`, protobuf-c's varint-length helpers. Both compilers keep them as branch chains; the winners use `lzcnt` and a multiply-shift (1.4–3.6× inlined latency). End to end, `get_packed_size` goes from 30.9 to 20.5 ns per point with clang (1.51×) and from 33.9 to 20.5 with gcc (1.65×). The full size + pack path, which also writes the bytes, goes from 72.7 to 63.2 ns (1.15×) and from 79.6 to 66.4 (1.20×). Packed bytes are identical. The gain comes from mispredicted branches, so it depends on the data: values of mixed magnitude here, while all-small values would see little change.
  - **`projects/stb_image`**: `stbi__high_bit` → `lzcnt`, decoded pixels identical. Nothing changes measurably because stb only calls it while parsing BMP headers.

## Quick start

Requires Linux x86-64, [uv](https://docs.astral.sh/uv/), a Rust toolchain, clang, gcc, binutils, and `llvm-mca`. `uv sync` builds the Rust harness.

```sh
uv sync
cp .env.example .env              # add MODEL_API_KEY (and BASETEN_API_KEY for Muse Spark Open)

uv run oqed search round_up_pow2_u32      # model-guided search (cheap contributor Spark, $1 cap), events in runs/<id>.jsonl
uv run oqed search clz_smear_u32 --volume spark --max-usd 5   # standard Spark, for demo runs
uv run oqed search parity_u32 --volume spark-open
uv run oqed try round_up_pow2_u32 my.s    # check a hand-written candidate
uv run oqed inline-bench <run-id>         # re-measure a run's winners inlined into loops, re-prove compiled bytes
uv run oqed optimize --from-run <run-id>  # write the run's proven winners to oqed.lock
cd myproject && uv run oqed scan          # free: every function oqed can search, ranked by calls (needs oqed.toml)
uv run oqed optimize --unsafe --max-usd 1 # search the project hottest-first; lock proven and tested-only winners
make CC=oqed-cc                            # build with oqed.lock applied (from a uv shell, or CC="uv run oqed-cc")
uv run oqed baseline round_up_pow2_u32    # clang/gcc output + llvm-mca estimates
uv run oqed scout                         # baseline table for every target
uv run oqed fake-run                      # synthetic run log for UI development
uv run pytest && (cd harness && cargo test)
```

Set `OQED_BENCH_CPU=<n>` to pin benchmarks to one core.

### UI

```sh
cd ui && npm install
npm run dev      # http://localhost:5173, serves every run in runs/
npm run build    # static site in ui/dist with the curated runs in runs/demo/
```

## Layout

```
src/oqed/      orchestrator: search loop, providers, prompts, testing, toolchain wrappers, ISA check, events, CLI
harness/       Rust (PyO3) extension: runs and times candidates in a forked, sandboxed child
benchmarks/    one directory per target: func.c + meta.toml
projects/      small real projects for end-to-end checks (protobuf-c packing, stb_image decoding)
tests/         test suite
ui/            web UI (Vite + React + TypeScript + Tailwind), reads runs/*.jsonl
runs/          JSONL event logs (curated demo runs in runs/demo/)
```

Each `runs/<run_id>.jsonl` file holds one event per line (`baseline_ready`, `candidate_proposed`, `candidate_rejected`, ...). It is the source of truth for the UI and for replays.

## Benchmark set

30 loop-free integer functions:

- bit tricks from *Hacker's Delight* (abs, sign, averages, power-of-two rounding, parity, bit reversal, ...)
- "missed idiom" variants that compilers don't recognize (smear + popcount for clz/log2, portable high multiply)
- clamp and saturating-add primitives modeled on FFmpeg's `av_clip*` / `av_sat_add32`
- hash finalizers (MurmurHash3 `fmix32`/`fmix64`, xxHash avalanche)
- six helpers copied from [stb_image](https://github.com/nothings/stb) v2.30 (`stbi_*`: PNG Paeth predictor, JPEG clamp, luma, CMYK blend, zlib bit reversal, BMP high bit), with `projects/stb_image/` as an end-to-end testbed
- protobuf-c's varint-length helpers (`pbc_uint32_size`, `pbc_uint64_size`), with `projects/protobuf_c/` as an end-to-end testbed

## Credits

- Bit-manipulation targets follow algorithms from Henry S. Warren Jr., *Hacker's Delight* (2nd ed.). The implementations here are our own.
- `stb_image.h` / `stb_image_write.h` by Sean Barrett (public domain or MIT), vendored unchanged in `projects/stb_image/`.
- `protobuf-c.c` / `protobuf-c.h` from [protobuf-c](https://github.com/protobuf-c/protobuf-c) v1.5.1 by Dave Benson and the protobuf-c authors (BSD 2-Clause), vendored unchanged in `projects/protobuf_c/`.
- `fmix32` / `fmix64` are from MurmurHash3 by Austin Appleby (public domain).
- xxHash avalanche constants come from xxHash by Yann Collet (BSD 2-Clause). The finalizer implementations here are our own.
- Built on [angr](https://angr.io), [Z3](https://github.com/Z3Prover/z3), [iced-x86](https://github.com/icedland/iced), and LLVM's `llvm-mca`.
