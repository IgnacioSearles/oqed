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
