// LevelDB on its in-memory Env: random-order Puts, a full compaction into
// SSTables, then Gets of every key and as many absent ones (bloom filters on).
//   driver           summary
//   driver --check   deterministic output only (counts and a hash of what Get returns)
//   driver --time    one number: ns per operation (Put + 2 Gets per key)
#include <chrono>
#include <cstdio>
#include <cstring>
#include <memory>
#include <string>
#include <vector>

#include "helpers/memenv/memenv.h"
#include "leveldb/db.h"
#include "leveldb/env.h"
#include "leveldb/filter_policy.h"
#include "leveldb/options.h"

static const int N = 60000;
static uint64_t xs = 0x9e3779b97f4a7c15ull;
static uint64_t rnd() {
  xs ^= xs << 13, xs ^= xs >> 7, xs ^= xs << 17;
  return xs;
}

struct Result {
  uint64_t hash = 0xcbf29ce484222325ull;
  int found = 0, missing = 0;
};

static Result once(const std::vector<std::string>& keys, const std::vector<std::string>& values,
                   const std::vector<std::string>& absent) {
  std::unique_ptr<leveldb::Env> env(leveldb::NewMemEnv(leveldb::Env::Default()));
  std::unique_ptr<const leveldb::FilterPolicy> bloom(leveldb::NewBloomFilterPolicy(10));
  leveldb::Options options;
  options.env = env.get();
  options.create_if_missing = true;
  options.filter_policy = bloom.get();
  options.compression = leveldb::kNoCompression;
  options.write_buffer_size = 1 << 20;  // several memtable flushes
  leveldb::DB* raw;
  if (!leveldb::DB::Open(options, "/db", &raw).ok()) std::abort();
  std::unique_ptr<leveldb::DB> db(raw);
  leveldb::WriteOptions wo;
  for (int i = 0; i < N; i++) {
    if (!db->Put(wo, keys[i], values[i]).ok()) std::abort();
  }
  db->CompactRange(nullptr, nullptr);
  Result r;
  leveldb::ReadOptions ro;
  std::string v;
  for (int i = 0; i < N; i++) {
    if (db->Get(ro, keys[(i * 7919) % N], &v).ok()) {
      r.found++;
      for (unsigned char c : v) r.hash = (r.hash ^ c) * 0x100000001b3ull;
    }
    if (db->Get(ro, absent[i], &v).IsNotFound()) r.missing++;
  }
  return r;
}

int main(int argc, char** argv) {
  bool check = argc > 1 && std::strcmp(argv[1], "--check") == 0;
  bool time_only = argc > 1 && std::strcmp(argv[1], "--time") == 0;
  std::vector<std::string> keys(N), values(N), absent(N);
  char buf[32];
  for (int i = 0; i < N; i++) {
    uint64_t k = rnd() % 100000000;
    std::snprintf(buf, sizeof buf, "%016llu", (unsigned long long)(k * 2));
    keys[i] = buf;
    std::snprintf(buf, sizeof buf, "%016llu", (unsigned long long)(k * 2 + 1));
    absent[i] = buf;
    values[i].resize(rnd() % 8 == 0 ? 400 : 100);
    for (char& c : values[i]) c = 'a' + rnd() % 26;
  }
  if (check) {
    Result r = once(keys, values, absent);
    std::printf("found %d missing %d hash %016llx\n", r.found, r.missing, (unsigned long long)r.hash);
    return 0;
  }
  double best = 1e30;
  for (int rep = 0; rep < 5; rep++) {
    auto t0 = std::chrono::steady_clock::now();
    Result r = once(keys, values, absent);
    double t = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
    if (r.found != N) std::abort();
    if (t < best) best = t;
  }
  if (time_only)
    std::printf("%.3f\n", best * 1e9 / (3.0 * N));
  else
    std::printf("%.1f ns/op\n", best * 1e9 / (3.0 * N));
  return 0;
}
