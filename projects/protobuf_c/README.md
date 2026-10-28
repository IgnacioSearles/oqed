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
through `unpack`, and prints an FNV-1a hash of the packed bytes. `--check` also
runs the coverage check (below).

## Result (laptop, Zen 3 under WSL2, best of 200, three runs each)

`oqed.lock` comes from `oqed optimize --unsafe --top 6 --max-usd 0.40` in this
directory (run `20261028-025628-cf64`, $0.32, contributor Spark). Nobody picked
the functions: `oqed scan` found 96 functions, 10 in scope, and the search took
the 6 the workload calls, most-called first.

| function | calls in the workload | result | inlined latency vs -O3 (clang / gcc) |
|---|---|---|---|
| `get_tag_size` | 6.8M | proven | 1.42× / 2.81× |
| `uint64_size` | 6.6M | proven | 3.57× / 3.48× |
| `uint32_size` | 4.8M | proven | 1.48× / 2.94× |
| `zigzag64` | 2.5M | no win (already 3 instructions) | |
| `sint64_size` | 1.6M | proven | 2.01× / 1.97× |
| `unzigzag64` | 4K | no win | |

All four winners are `lzcnt` plus a multiply and shift in place of a branch
chain. Every function in scope here is loop-free, so all four are proven and
`--unsafe` added nothing.

`make compare` (ns per point; packed bytes identical in every build):

| compiler | get_packed_size plain | oqed-cc | | size + pack plain | oqed-cc | |
|---|---|---|---|---|---|---|
| clang | 30.9 | 20.5 | **1.51×** | 72.7 | 63.2 | **1.15×** |
| gcc | 33.9 | 20.5 | **1.65×** | 79.6 | 66.4 | **1.20×** |

oqed-cc proves all four substitutions against protobuf-c's own functions with
each compiler, and none stops a loop from vectorizing. An earlier lock with only
the two hand-picked functions (`uint64_size`, `uint32_size`) gave 1.57× / 1.24×
on `get_packed_size`; adding `get_tag_size` and `sint64_size` mostly helps gcc.
The gain comes from branch mispredictions on values of mixed magnitude: a batch
where every value fits in one byte would see little change.

## Functions that write memory (`--unsafe`, project oracle)

`oqed optimize --unsafe field_is_zeroish uint32_pack tag_pack uint64_pack
sint64_pack` (run `20261028-135748-51da`, $0.29) searched the hottest functions that take
pointers. These can't be run alone on random inputs, so each candidate was
built into a copy of this project and judged by `pbc_profile --check` (packed
bytes, hash and round trip must match the C build exactly) and timed with
`pbc_profile --time` against a baseline build, both with clang.

| function | calls | candidates the tests rejected | result (size + pack, whole benchmark) |
|---|---|---|---|
| `field_is_zeroish` | 12.3M | 2 | no win: every passing candidate was slower than the compiler's jump table |
| `uint32_pack` | 6.2M | 7 | 1.07×, tested |
| `tag_pack` | 4.2M | 0 | 1.08×, tested |
| `uint64_pack` | 3.3M | 7 | 1.06×, tested |
| `sint64_pack` | 0.8M | 13 | no win |

All three winners are varint encoders. They are locked as `tested`: oqed-cc
substitutes them only while the function and the declarations it uses hash to
what was tested, and says `UNSAFE` when it does. Size + pack, ns per point,
median of 5 runs:

| compiler | plain | proven entries | proven + tested |
|---|---|---|---|
| clang | 75.4 | 64.3 (1.17×) | **61.8 (1.22×)** |
| gcc | 79.1 | 66.6 (1.19×) | 66.8 (1.18×) |

The tested entries help clang, the compiler the oracle used, and do nothing
for gcc.

### The coverage check

`--check` first packed only the telemetry batch (5 field types, proto3 and
repeated), so most of protobuf-c's packing code never ran under the oracle.
It now also runs the coverage check (`gen_cover.py` writes `cover.pb-c.{h,c}`):
500 random `Cover` messages with every scalar type as required, optional,
proto3 and repeated (packed and unpacked) fields, strings, bytes, a oneof,
nested and repeated messages, NULL strings and sub-messages, field numbers up
to 536870911, and values biased to edge cases. Each goes through
`get_packed_size`, `pack`, `pack_to_buffer`, `unpack` and back, and is unpacked
again with a third of its fields unknown. The check prints hashes of
everything packed. Under gcov it runs every line of the packing and sizing
code except `ASSERT_NOT_REACHED` defaults. It takes 0.16 s.

### Functions that call others

With whole-function substitution (a candidate that uses the stack or calls
other functions replaces the function as a `naked` one), 67 of the 96
functions are in scope. Two capped runs searched the hottest of them
(`oqed optimize --unsafe`, waves: 6 candidates, more only while improving;
$0.08 + $0.27):

| function | calls | candidates | result |
|---|---|---|---|
| `unlabeled_field_get_packed_size` | 8.2M | 6 | no win |
| `required_field_get_packed_size` | 6.8M | 9 | no win |
| `required_field_pack` | 4.2M | 6 | no win |
| `unlabeled_field_pack` | 4.1M | 9 | no win |
| `prefixed_message_pack` | 0.8M | 9 | no win |
| `protobuf_c_message_pack` | 0.8M | 14 | **1.08×, tested** |

The `protobuf_c_message_pack` winner rewrote the whole field loop: one
dispatch on the label per field, calls straight to the per-label packers,
callee-saved registers instead of the stack. It passed the old `--check`. The
coverage check rejects it: with unknown fields, the message packs 16641 bytes
where its size says 18656. Its unknown-fields loop keeps its counter in `ecx`
across `call unknown_field_pack`, and `ecx` is caller-saved. The old test had
no unknown fields. It is out of the lock. The three varint encoders pass the
coverage check with both compilers.

The lock now has 7 entries: 4 proven, 3 tested. Size + pack, ns per point,
median of 3 runs:

| compiler | plain | oqed.lock |
|---|---|---|
| clang | 73.8 | **61.2 (1.21×)** |
| gcc | 78.8 | **67.5 (1.17×)** |

`--check` prints the same with and without the lock, under both compilers.
