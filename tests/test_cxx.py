"""C++ projects: scan (namespaces, classes, overloads, references), slices,
oqed-cxx substitution and the exceptions guard, and a search on a C++ target."""

import asyncio
import os
import subprocess
import sys

import pytest

from oqed import lock, project
from oqed.events import read_events
from oqed.providers import MockProvider
from oqed.search import SearchConfig, run_search
from oqed.targets import load_target

LIB_H = """#pragma once
#include <cstddef>
#include <cstdint>
#include <string>
namespace demo {
struct Slice {
  const char* data_;
  size_t size_;
  size_t size() const { return size_; }
};
class Coder {
 public:
  explicit Coder(uint32_t k) : k_(k) {}
  uint32_t mix(uint32_t x) const;
  bool has(uint32_t x) const { return ((x * 2654435761u) >> 7 & k_) != 0; }
 private:
  uint32_t k_;
};
uint32_t hot(uint32_t x);
uint32_t hot(uint32_t x, uint32_t y);
inline int varlen(uint64_t v) {
  int n = 1;
  while (v >= 128) { v >>= 7; n++; }
  return n;
}
size_t total(const Slice& s, uint32_t extra);
void put(std::string* dst, uint32_t v);
}  // namespace demo
"""
LIB_CC = """#include "lib.h"
namespace demo {
namespace {
uint32_t rot(uint32_t x) { return (x << 13) | (x >> 19); }
}  // namespace
uint32_t Coder::mix(uint32_t x) const { return rot(x ^ k_) * 0x9e3779b1u; }
uint32_t hot(uint32_t x) {
  return x < 16 ? x * 3 : (x < 256 ? x * 5 + 1 : (x < 65536 ? x * 7 + 2 : x * 9 + 3));
}
uint32_t hot(uint32_t x, uint32_t y) { return hot(x) ^ y; }
size_t total(const Slice& s, uint32_t extra) { return s.size() + extra; }
void put(std::string* dst, uint32_t v) {
  char buf[5];
  int n = 0;
  while (v >= 128) { buf[n++] = char(v | 128); v >>= 7; }
  buf[n++] = char(v);
  dst->append(buf, n);
}
}  // namespace demo
"""
MAIN_CC = """#include <cstdio>
#include "lib.h"
int main(int argc, char** argv) {
  demo::Coder c(0x5a);
  uint32_t acc = 0;
  uint64_t lens = 0;
  std::string out;
  demo::Slice s{"abc", 3};
  for (uint32_t i = 0; i < 20000000; i++) {
    acc += demo::hot(i * 2654435761u >> (i & 31)) + c.mix(i) + c.has(i) + demo::hot(i, acc);
    lens += demo::varlen((uint64_t)i * i) + demo::total(s, i & 7);
    if ((i & 1023) == 0) out.clear();
    demo::put(&out, i);
  }
  unsigned h = 0;
  for (char ch : out) h = h * 31 + (unsigned char)ch;
  printf("%u %llu %zu %08x\\n", acc, (unsigned long long)lens, out.size(), h);
  return 0;
}
"""

# varlen: (floor(log2(v|1)) / 7) + 1, the division as a multiply (exact for 0..63).
VARLEN = """\tor rdi, 1
\tlzcnt rax, rdi
\tmov ecx, 63
\tsub ecx, eax
\timul ecx, ecx, 37
\tshr ecx, 8
\tlea eax, [rcx+1]
\tret
"""
# Coder::mix: `this` in rdi, k_ at offset 0.
MIX = """\tmov eax, esi
\txor eax, dword ptr [rdi]
\trol eax, 13
\timul eax, eax, -1640531535
\tret
"""
# put as a whole function: encode into a stack buffer, then call std::string::append.
PUT = """\tpush rbx
\tsub rsp, 16
\tmov rbx, rdi
\txor edx, edx
.Lloop:
\tcmp esi, 128
\tjb .Ldone
\tmov eax, esi
\tor eax, 128
\tmov byte ptr [rsp+rdx], al
\tinc edx
\tshr esi, 7
\tjmp .Lloop
.Ldone:
\tmov byte ptr [rsp+rdx], sil
\tinc edx
\tmov rdi, rbx
\tmov rsi, rsp
\tcall _ZNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEE6appendEPKcm
\tadd rsp, 16
\tpop rbx
\tret
"""


@pytest.fixture(scope="module")
def cxx(tmp_path_factory):
    root = tmp_path_factory.mktemp("cxx")
    (root / "lib.h").write_text(LIB_H)
    (root / "lib.cc").write_text(LIB_CC)
    (root / "main.cc").write_text(MAIN_CC)
    (root / "oqed.toml").write_text(
        'build = "$CXX -O2 -march=x86-64-v3 -o app main.cc lib.cc"\nrun = "./app"\n'
    )
    p = project.load(root)
    return p, {f.name: f for f in project.scan(p).functions}


def test_scan_finds_namespaces_classes_overloads_and_references(cxx):
    _, by = cxx
    assert {"put", "varlen", "demo.hot.1", "demo.hot.2", "mix", "has", "total", "rot", "size", "main"} <= set(
        by
    )
    assert by["put"].time > 0.05  # sampled by mangled symbol
    # A method takes `this` and reads memory; a reference is a pointer.
    assert by["mix"].oracle == by["has"].oracle == by["put"].oracle == "project"
    assert by["varlen"].oracle == "harness" and by["varlen"].location == "lib.h:21"
    mix = load_target(by["mix"].target)
    assert (mix.lang, mix.symbol, mix.qualified, mix.args, mix.ret) == (
        "c++", "_ZNK4demo5Coder3mixEj", "demo::Coder::mix", ["u64", "u32"], "u32"
    )  # fmt: skip
    # The out-of-line method brings its class and the anonymous-namespace helper, in their namespaces.
    src = mix.c_source
    assert "class Coder" in src and "namespace {" in src and "rot(uint32_t x)" in src
    assert "__attribute__((used)) uint32_t Coder::mix" in src
    hot2 = load_target(by["demo.hot.2"].target)
    assert hot2.args == ["u32", "u32"] and hot2.qualified == "demo::hot"


def _entry(t, asm, status):
    return lock.LockEntry(
        function=t.function, qualified=t.qualified, args=t.args, ret=t.ret, march="x86-64-v3", asm=asm,
        status=status, speedup=1.0, benchmark=t.name, run="test", candidate="test", oracle=t.oracle,
        slice_sha=t.slice_sha if t.oracle == "project" else None,
    )  # fmt: skip


def _build(root, *flags, cc="clang++"):
    env = {
        **os.environ,
        "OQED_CACHE": str(root / "cache"),
        "OQED_AS_CXX": "1",
        "OQED_LOCK": str(root / "oqed.lock"),
    }
    cmd = [sys.executable, "-m", "oqed.cc", f"--cc={cc}", "-O2", "-march=x86-64-v3", *flags]
    out = subprocess.run(
        cmd + ["-o", "app", "main.cc", "lib.cc"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stderr


@pytest.mark.parametrize("cc", ["clang++", "g++"])
def test_oqed_cxx_substitutes_proves_and_guards_exceptions(cxx, cc):
    p, by = cxx
    root = p.root
    (root / "oqed.lock").unlink(missing_ok=True)
    _build(root, cc=cc)
    run = lambda: subprocess.run(["./app"], cwd=root, capture_output=True, text=True, check=True).stdout  # noqa: E731
    want = run()
    t = {n: load_target(by[n].target) for n in ("varlen", "mix", "put")}
    entries = [
        _entry(t["varlen"], VARLEN, "tested"),
        _entry(t["mix"], MIX, "tested"),
        _entry(t["put"], PUT, "tested"),
    ]
    (root / "oqed.lock").write_text(lock.dumps(entries))
    err = _build(root, cc=cc)
    # varlen's C has a loop, so the proof can't decide; differential testing passes.
    assert "varlen: UNSAFE: substituted on testing only (proof unknown" in err, err
    assert "mix: UNSAFE: substituted on testing only" in err, err
    # put calls std::string::append, which may throw through the asm.
    assert "put: kept C (calls out and may unwind through the asm" in err, err
    assert run() == want
    err = _build(root, "-fno-exceptions", cc=cc)
    assert "put: UNSAFE: substituted on testing only" in err, err
    assert run() == want
    dis = subprocess.run(
        ["objdump", "-d", "--no-show-raw-insn", "app"], cwd=root, capture_output=True, text=True, check=True
    )
    body = dis.stdout.split("<_ZN4demo3putEPNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEEj>:")[1]
    assert "append" in body.split("\n\n")[0]


# demo::hot(x), branchless.
HOT = """\tlea eax, [rdi+rdi*2]
\tlea ecx, [rdi+rdi*4+1]
\tcmp edi, 16
\tcmovae eax, ecx
\tlea ecx, [rdi*8]
\tsub ecx, edi
\tadd ecx, 2
\tcmp edi, 256
\tcmovae eax, ecx
\tlea ecx, [rdi+rdi*8+3]
\tcmp edi, 65536
\tcmovae eax, ecx
\tret
"""


def test_search_on_a_cxx_function(cxx, tmp_path):
    _, by = cxx
    t = load_target(by["demo.hot.1"].target)
    mock = MockProvider([f"<explanation>three cmovs</explanation><asm>\n{HOT}</asm>"])
    cfg = SearchConfig(waves=False, candidates=1, concurrency=1, tricks=0)
    run_id = asyncio.run(run_search([t], mock, mock, cfg, runs_dir=tmp_path))
    events = read_events(tmp_path / f"{run_id}.jsonl")
    base = next(e for e in events if e.type == "baseline_ready")
    assert "namespace demo" in base.source
    # Tested against the C++ compiled by clang++ and g++ and timed inlined (it is correct, but
    # clang's branches are faster on these inputs), and proven if it is faster.
    assert {e.stage for e in events if e.type == "candidate_rejected"} <= {"benchmark"}, events
    assert any(e.type == "candidate_benchmarked" for e in events)
    assert all(e.status == "proven" for e in events if e.type == "candidate_verified")
