# oqed

```
faster than -O3. proven equivalent (when it can be).
```

oqed is an LLM-guided superoptimizer for x86-64. point it at a C or C++
project and it finds the hot functions, asks Meta's Muse models for faster
assembly, throws away everything that isn't correct, and keeps what beats
`clang -O3` and `gcc -O3`. then you build with `CC=oqed-cc` and that's it,
your source doesn't change.

built for the Meta Global AI Developer Hackathon (Push the Frontier).

**live UI with the real runs: [ignaciosearles.github.io/oqed](https://ignaciosearles.github.io/oqed/)**

## results

real open-source projects, cloned as they are. whole program, plain build vs
oqed build, 11 alternating runs, output identical every time:

```
project       hottest function             clang     gcc      spend
protobuf-c    message_get_packed_size      1.269x    1.320x   $14.68   7 entries, 2 proven
zlib          longest_match (88%)          1.172x    1.145x   $15.85   2 entries
cJSON         parse_string / parse_value   1.072x    -        $0.85    3 entries
LevelDB C++   BytewiseComparator::Compare  1.046x    0.993x   $2.41    1 entry
libyaml       fetch_plain_scalar           no win             $2.43    99 candidates, none faster
lz4           compress_generic_internal    out of reach       -        hot code is 4,000+ instructions
```

entries that aren't proven were judged by the project's own tests and
benchmark (`--unsafe`, they touch memory, the prover doesn't do memory yet).
protobuf-c's 2 proven entries alone give 1.171x / 1.125x.

single functions, proven equivalent for every input, latency inlined in a loop:

```
clz_smear_u32       6.46x    smear + popcount -> lzcnt
parity_u32          3.49x
round_up_pow2_u32   3.24x
```

numbers come from a WSL2 laptop, not a quiet benchmark box. the per-project
write-ups are in [sweep/](sweep/README.md).

## how it works

```
 your project ──► oqed scan: build it, run the workload, profile, slice every hot function
                     │
                     ▼
   Muse Spark (lots of parallel candidates) + Muse Wonder (thinks hard when the search stalls)
                     │  asm + one line on the trick
                     ▼
   assemble ─► ISA check ─► tests ─► SMT proof ─► benchmark vs clang AND gcc
                     │  every failure goes back to the model: assembler line, counterexample,
                     │  ABI violation, crash site + registers, diff of the output
                     ▼
   oqed.lock ─► make CC=oqed-cc   (re-proves each entry against your source on every build)
```

- **models:** `muse-spark-1.3` on the Meta Model API writes most candidates,
  `muse-wonder-1.0` gets the whole search history when progress stalls (it
  wrote zlib's winner). Muse Spark Open on Baseten can be the volume tier too.
- **proofs:** symbolic execution (angr) + Z3 over every ABI-valid input.
  anything it can't decide is labelled "tested, not proven", never "proven".
  a suite of deliberately wrong rewrites in `tests/test_verify.py` makes sure.
- **fair fight:** candidates can only use instructions the baseline `-march`
  has, checked by decoding the machine code.
- **oqed-cc / oqed-cxx:** drop-in compiler wrappers. they preprocess with your
  flags, swap the function body for the asm, prove it against your function
  as it is right now, keep the C if anything is off (stale entry, loop that
  stops vectorizing, internal error). no network calls at build time.

the long version, with what each project taught it: [docs/DETAILS.md](docs/DETAILS.md).

## what we learned

llms are decent at this, sometimes. not magic.

- **the wins are where the compiler is stuck, not where it's dumb.** a branch
  chain that should be one `lzcnt`, a `memcmp` call on 16-byte keys that
  should be two 8-byte compares, a hash-chain walk whose next load could start
  earlier. small, specific, and the model finds them fast.
- **big functions mostly fail.** at 300+ instructions (zlib's `deflate_slow`,
  libyaml's scanners) the model writes code that crashes or is subtly wrong,
  and lz4's hot code is too big to even try. in the zlib run 85 of 101
  candidates were rejected.
- **so the checker is the product.** a `longest_match` that found shorter
  matches made zlib 1.63x faster and the `.gz` 0.6% bigger; tests that only
  check the round trip would have shipped it. same for measurement: two of
  LevelDB's three "wins" and lz4's one didn't hold up when re-measured alone.
- **it's the last few percent.** the right algorithm or data structure beats
  any of this by a mile, and oqed can only touch the project's own code (a
  third of LevelDB's time, a quarter of cJSON's; the rest is libc and malloc).
  where it fits is mature hot code whose algorithm is already settled:
  compressors, parsers, serializers. and several of the tricks could be
  written back in C once you know them, so it's also a way to find out where
  your compiler leaves time on the table.
- **it's cheap.** $15.85 of model calls for 1.17x on zlib.

## run it

needs linux x86-64, [uv](https://docs.astral.sh/uv/), rust, clang, gcc,
binutils and `llvm-mca`.

```sh
uv sync                    # also builds the rust harness
cp .env.example .env       # put your MODEL_API_KEY in
```

one function:

```sh
uv run oqed search parity_u32            # cheap Spark, $1 cap, logs to runs/<id>.jsonl
uv run oqed optimize --from-run <id>     # proven winners -> oqed.lock
```

a whole project:

```sh
cd ~/some-c-project
uv run --project ~/oqed oqed optimize --unsafe --max-usd 5    # init, scan, search, lock, measure
make CC=oqed-cc                                               # build with it (CXX=oqed-cxx for C++)
```

`oqed optimize` writes `oqed.toml` (how to build, run and test the project;
the model proposes it and oqed checks it actually works), scans, searches the
hottest functions under the spending cap, writes `oqed.lock` and measures the
whole program. `oqed scan` alone is free.

the UI:

```sh
cd ui && npm install && npm run dev      # localhost:5173, shows every run in runs/, live
```

### env

```
MODEL_API_KEY     required   Meta Model API key (Muse Spark, Muse Wonder)
BASETEN_API_KEY   optional   Muse Spark Open on Baseten (--volume spark-open)
OQED_CC           optional   compiler oqed-cc wraps (default clang), or CC="oqed-cc --cc=gcc"
OQED_CXX          optional   compiler oqed-cxx wraps (default clang++)
OQED_BENCH_CPU    optional   pin benchmarks to one core
OQED_CACHE        optional   cache dir (default ~/.cache/oqed)
OQED_QUIET        optional   silence oqed-cc's notes on stderr
```

every run has a spending cap (`--max-usd`, default $1) and prints what it spent.

## layout

```
src/oqed/     search loop, prompts, providers, verifier, oqed-cc, CLI
harness/      rust extension that runs and times candidates in a sandboxed child
benchmarks/   30 small integer functions (Hacker's Delight, missed idioms, hashes, ...)
projects/     vendored testbeds (protobuf-c, stb_image)
sweep/        real projects: configs, locks, reports
ui/           web UI (vite + react), reads runs/*.jsonl
runs/         one event log per run (runs/demo/ is what the hosted UI shows)
```

`uv run pytest && (cd harness && cargo test)` runs the tests.

## credits

Hacker's Delight (Henry S. Warren Jr.) for the bit tricks, stb_image (Sean
Barrett), protobuf-c, MurmurHash3, xxHash, and the projects in the sweep:
zlib, cJSON, LevelDB, libyaml, lz4. built on [angr](https://angr.io),
[Z3](https://github.com/Z3Prover/z3), [iced-x86](https://github.com/icedland/iced)
and LLVM's `llvm-mca`. full credits in [docs/DETAILS.md](docs/DETAILS.md#credits).
