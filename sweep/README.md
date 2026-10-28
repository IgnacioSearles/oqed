# Library sweep

Real C libraries, cloned as they are, set up by `oqed init` (the model writes
`oqed.toml`, oqed checks it by running it) and optimized by
`oqed optimize <dir> --unsafe`. Each directory holds the config and, where
something was found, the lock and `report.json`: a plain build against an
oqed-cc build, same test result and output files, 11 alternating runs.

| library | commit | init | hottest function (share of the workload) | searched | spend | whole program |
|---|---|---|---|---|---|---|
| zlib | 71747d1 | 3 attempts, $0.003 | `longest_match` (88%) | top 3 | $0.35 | **1.026x**, faster in 11 of 11 runs, outputs identical (1 tested entry) |
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
