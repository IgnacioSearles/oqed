# Library sweep

Real C libraries, cloned as they are, set up by `oqed init` (the model writes
`oqed.toml`, oqed checks it by running it) and optimized by
`oqed optimize <dir> --unsafe`. Each directory holds the config and, where
something was found, the lock and `report.json`: a plain build against an
oqed-cc build, same test result and output files, 11 alternating runs.

| library | commit | init | hottest function (share of the workload) | searched | spend | whole program |
|---|---|---|---|---|---|---|
| protobuf-c (v1.5.1 testbed, `projects/protobuf_c`) | - | hand-written config | `protobuf_c_message_get_packed_size` (31% with what is inlined into it) | top 16, Muse Wonder writes 1 of each first wave | $14.68 | **1.269x**, faster in 11 of 11 runs, coverage-test output identical (7 entries: 2 proven, 5 tested). The 2 proven entries alone: **1.171x**, 11 of 11 |
| zlib | 71747d1 | 3 attempts, $0.003 | `longest_match` (88%) | top 3 | $0.35 | **1.026x**, faster in 11 of 11 runs, outputs identical (1 tested entry) |
| cJSON | 6d9f244 | 2 attempts, $0.004 | `parse_string` (3%); the library's own code is ~25% of the workload, the rest is libc (`sprintf`/`strtod` for doubles) | top 6 | $0.85 | **1.072x**, faster in 11 of 11 runs, output hash identical (3 tested entries) |
| libyaml | 90a56d4 | 4 attempts, $0.005 (driver and input generator written as files) | `yaml_parser_fetch_plain_scalar` (19%) | top 8 | $2.43 | no win: 99 candidates, 31 correct but not faster; the hot code is in scanner functions of 300-1,500 instructions |
| lz4 | 5e08bd2 | 1 attempt, $0.001 | `LZ4HC_compress_generic_internal` (62%) | none | - | out of reach: the hot code is force-inlined into functions of 4,000-8,600 instructions; what oqed can take is under 5% each |

What the sweep caught along the way:

- zlib, first run: a `longest_match` that finds shorter matches. The round
  trip passed, the program was 1.63x faster, and the `.gz` came out 0.6%
  larger. The project oracle now compares every file the test writes with the
  C build's, byte for byte; the second and third runs rejected such
  candidates.
- lz4, first run: a 1.04x win that measured 0.977x in the report. It was
  noise: a 90 ms workload, timed while other candidates built. Benchmarks now
  run alone, and a candidate must win 6 of 7 paired runs.
- cJSON: most of the 1.072x is `parse_value` (1.064x alone, though it is 1.9%
  of the samples). clang inlines `parse_array` and `parse_number` into an
  890-byte `parse_value` that every level of the recursive descent goes
  through; the model's version is a 254-byte first-byte dispatcher that
  tail-calls them, so they become functions of their own. A real gain with
  identical output, and part of it comes from the outlining; marking those
  helpers noinline in C might get some of it too. `ensure` gives 1.008x and
  `buffer_skip_whitespace` 1.016x.
- cJSON was the first run with assembler errors fed back line by line and a
  repair turn: 6 of 67 rejections were assembler errors (9%), against 47 of 74
  (64%) in the lz4 and zlib runs. The ABI check is now the most common
  rejection (14, reading caller-saved registers after calls).
- libyaml: `oqed init` failed four times while the model quoted a C driver and
  a Python generator inside a TOML string inside a shell command. It now
  writes them as files in `oqed_support/` (copied here), and the scan never
  searches them: the driver's own FNV hash was 14% of the samples and would
  have been the easiest "win".
- libyaml was the first run where calling-convention violations go back to the
  model with each one explained (bytes pushed, which register a call
  clobbered). They were still the most common rejection (39 of 99, mostly
  misaligned calls), but 14 of 22 repaired candidates then passed the check.
- protobuf-c, the bigger run: a fresh copy with no lock, the 16 hottest
  functions, and Muse Wonder writing the first candidate of every function
  (`oqed optimize --top 16 --wonder 1 --max-usd 15`). Wonder made 21 of the
  208 model calls and $13.28 of the $14.68, and wrote 5 of the 7 winners: all
  five whole-function rewrites (`protobuf_c_message_get_packed_size`,
  `protobuf_c_message_pack`, and the required/unlabeled/repeated field sizers).
  Contributor Spark (187 calls, $1.40) found the two proven ones, the varint
  helpers `uint64_size` (3.3x inlined latency) and `get_tag_size` (1.4x).
  Rejections: 45 correct but not faster, 37 wrong output (the coverage test
  caught them), 37 calling-convention violations, 24 assembler errors.
