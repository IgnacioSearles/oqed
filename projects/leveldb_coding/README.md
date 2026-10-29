# LevelDB's encoding code

LevelDB's `util/coding.cc`, `util/hash.cc` and `util/bloom.cc` (google/leveldb
bb74ef7, BSD license in `LICENSE`) in C: the C++ bodies with casts and
namespaces removed. It checks whether oqed finds wins in code from a real C++
project before oqed builds C++ support. `oqed_support/bench.c` is the
workload, a write path like LevelDB's: write batch (length-prefixed key and
value), memtable entries (`VarintLength`, varint lengths, fixed64 tag), a
prefix-compressed data block parsed back entry by entry, a bloom filter over
the keys probed with every key and as many absent ones, and version edits
(varint64). Keys are db_bench's 16-digit keys, values 0-400 bytes.
`--check` prints the sizes and a hash of every byte written; oqed's project
test requires it to match the C build exactly.

`oqed_support/buf.h` stands in for `std::string`: like libstdc++'s `append`,
the capacity check and the copy are inline at each call site and only growing
is a call. (A first version had `append` out of line in another file; oqed's
`PutVarint32` then won 1.18x mostly by skipping that call, which real C++
wouldn't pay. That run is kept in `experiments/leveldb/` and not counted.)

```sh
make bench               # clang; make bench CC=gcc
uv run oqed scan         # where the time goes
uv run oqed optimize --unsafe --top 8 --max-usd 1
uv run oqed report
```

## Result

`oqed optimize --unsafe --top 8`, contributor Spark with Muse Wonder on
stalls, $0.47: 3 of 8 functions won, all tested (the project's test judges
them; none is register-only). Each entry measured alone, 11 paired runs,
output identical in every row:

| entry | clang | gcc |
|---|---|---|
| `PutVarint32` | 1.147x | 1.024x |
| `Hash` | 1.015x | 1.019x |
| `BloomHash` | 1.019x | 1.007x |
| all 3 | **1.153x** (112.0 -> 97.1 ns/record) | **1.031x** (97.9 -> 95.0) |

`PutVarint32` encodes into a 5-byte stack buffer and appends `ptr - buf`
bytes. clang compiles that append to a `memcpy` call for 1-5 bytes; gcc
inlines the copy, which is why gcc's plain build is 13% faster here. The
model's version checks capacity for 5 bytes once and stores the varint bytes
straight into the buffer. The win is clang's missed copy, so it is small
under gcc.

Not found: the bloom filter (`KeyMayMatch`, `CreateFilter`: 27% of the time)
does a 32-bit `div` per probe, 6 probes per key; a multiply by a reciprocal
computed once per call would give the same remainder. Every candidate there
failed the test (wrong bloom bits) or wasn't faster.
