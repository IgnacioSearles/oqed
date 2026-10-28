# protobuf-c testbed

A real project where an oqed substitution sits on the hot path. `protobuf-c.c`,
`protobuf-c.h` and `LICENSE` are vendored unchanged from
[protobuf-c](https://github.com/protobuf-c/protobuf-c) v1.5.1 (`185beed`,
BSD-2-Clause).

```
make bench            # clang
make bench CC=gcc
make compare          # plain build vs oqed-cc build with oqed.lock; packed bytes must be identical
```

Before writing a message, protobuf-c works out how many bytes each integer
field takes as a varint. `uint32_size` and `uint64_size` do it with a chain of
comparisons (`v < 1<<7`, `v < 1<<14`, ...), and clang and gcc keep it as a
chain of branches. On values of mixed magnitude those branches mispredict. The
branch-free form is `lzcnt` and a multiply. The two functions are copied into
`benchmarks/pbc_uint32_size` and `benchmarks/pbc_uint64_size`.

`telemetry.proto` describes the workload: a batch of 4096 points with a
timestamp, a series hash, a value, a zigzag delta and flags. `protoc-c` is not
needed: `telemetry.pb-c.{h,c}` are written by hand in the form it generates.
Values are mixed the way metrics are: 40% under 128, 30% under 2^20, 20% under
2^40 and 10% negative. `pbc_bench` reports the best time per point for
`get_packed_size` and for `get_packed_size` + `pack`, checks a round trip
through `unpack`, and prints an FNV-1a hash of the packed bytes.

## Result (laptop, Zen 3 under WSL2, best of 200, three runs each)

`oqed.lock` comes from run `20261028-021819-67bd` (24 contributor-Spark candidates
per function, $0.08):

| function | oqed asm | inlined latency vs -O3 |
|---|---|---|
| `uint64_size` | `lzcnt rax, rdi; lea edx, [rax+rax*8]; shl edx, 2; mov eax, 2560; sub eax, edx; shr eax, 8` | 2.75× |
| `uint32_size` | `lzcnt eax, edi; lea ecx, [rax+rax*8]; mov eax, 352; sub eax, ecx; shr eax, 6` | 1.45× |

`make compare` (ns per point; packed bytes identical in every build):

| compiler | get_packed_size plain | oqed-cc | | size + pack plain | oqed-cc | |
|---|---|---|---|---|---|---|
| clang | 32.4 | 20.6 | **1.57×** | 74.9 | 64.9 | **1.15×** |
| gcc | 33.4 | 26.9 | **1.24×** | 79.3 | 73.0 | **1.09×** |

oqed-cc proves both substitutions against protobuf-c's own functions with each
compiler. Neither one stops a loop from vectorizing. The gain comes from
branch mispredictions on values of mixed magnitude: a batch where every value
fits in one byte would see little change, since the branchy code predicts
well there.
