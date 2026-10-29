# LevelDB (C++)

google/leveldb at bb74ef7, built by CMake as it is, with `CC=oqed-cc
CXX=oqed-cxx`. The workload is `oqed_support/driver.cc` (hand-written, not by
`oqed init`): an in-memory Env, 60,000 random Puts, a full compaction, then a
Get of every key and of a missing neighbour. `--check` prints the counts and
a hash of every value read; `--time` prints ns per operation, the best of 5
repetitions.

```
git clone https://github.com/google/leveldb ~/oqed-sweep/leveldb && cd ~/oqed-sweep/leveldb
git checkout bb74ef7
cp -r ~/oqed/sweep/leveldb/{oqed.toml,oqed_support} .
uv run --project ~/oqed oqed scan                # 717 functions, 222 oqed can search
uv run --project ~/oqed oqed optimize --unsafe --max-usd 3 DecodeEntry Iter.Seek ...
uv run --project ~/oqed python ~/oqed/sweep/leveldb/compilers.py ~/oqed-sweep/leveldb
```

**Scan.** 717 function definitions, 222 in scope. The top of the list:
`DecodeEntry` 4.9%, `Block::Iter::Seek` 3.4%, `Block::Iter::ParseNextKey`
3.3%, `crc32c::Extend` 2.9%, the two comparators and `Slice::compare` ~1.5%
each. Only about a third of the samples are in LevelDB's own code; the rest is
libc (`memcmp`, `memcpy`), malloc and locks.

**Search.** Muse Spark (contributor) with Muse Wonder on stalls, the 8 hottest
functions, then `Extend` again after a fix to the acceptance rule: 103
candidates in those two runs, $2.41 in all including $0.66 on two earlier
runs stopped for oqed bugs (below). Four entries were accepted; re-checked
afterwards, one holds up.

| entry | in the search | re-checked alone (11 alternating runs) |
|---|---|---|
| `BytewiseComparatorImpl::Compare` | 1.03x | **1.066x**, faster in 10 of 11 |
| `crc32c::Extend` (hardware `crc32` instead of the tables) | 1.04x | 0.987x, faster in 3 of 11: dropped |
| `InternalKeyComparator::Compare` | 1.06x | 0.974x: dropped (see below) |

**Result** (`compilers.json`, the one entry, tested only):

| compiler | plain | oqed | whole program |
|---|---|---|---|
| clang++ | 1018.9 ns/op | 974.0 ns/op | **1.046x**, faster in 10 of 11 runs, output identical |
| g++ | 1010.9 ns/op | 1018.1 ns/op | 0.993x, faster in 5 of 11: no gain |

With both comparator and `Extend` entries the same report gave 1.066x, 11 of
11, and an earlier one 1.083x; the comparator is the whole of it. The
model's comparator compares 8 bytes at a time and orders the first differing
word with `bswap`, where clang's code calls `memcmp`: on the driver's 16-byte
keys that call is most of the cost, and `memcmp`'s time was counted in libc,
not in the comparator's 1.4%. g++'s build gains nothing from it.

**What LevelDB found in oqed** (each fixed, with a test):

- LevelDB builds with `-Werror`; clang warned that the preprocessed file left
  `-I` unused, so every candidate failed to build.
- `class Block::Iter {` is defined outside `Block`: the scan named it
  `leveldb::Iter`, and oqed-cxx couldn't find its methods.
- `Iter::Seek`'s candidates called the inline `Iter::CorruptionError`, which
  has no symbol when every call is inlined: oqed-cxx now marks such callees
  `used`.
- `InternalKeyComparator` has `Compare(const Slice&, const Slice&)` and
  `Compare(const InternalKey&, const InternalKey&)`; as pointers both are
  `(this, ptr, ptr) -> int`. The entry could land on the second one (an
  inline in a header), which only ran correctly because `std::string` and
  `Slice` both start with a pointer and a size. Lock entries now record the
  mangled symbol. Found by symbol, the right overload is not faster.
- The whole program's speed drifted by up to 28% during a run (965-1234 ns/op). Candidates are
  timed against a C build alternately, so they were judged right, but the UI
  plotted them against a baseline from the start, and a spread check rejected
  real 4% wins. Project candidates are now plotted, ranked and summarized by
  their paired speedup, and accepted on the sign test plus a 2% median gain.
