import os
import subprocess
import sys

import pytest

from oqed import lock, project
from oqed.targets import load_target

LIB_H = """
typedef unsigned int u32;
enum { SHIFT = 3 };
static inline u32 helper(u32 x) { return x >> SHIFT; }
u32 hot(u32 x);
int cold(int a, int b);
int reads_table(unsigned char c);
struct point { int x, y; };
int uses_struct(const struct point *p);
"""
LIB_C = """
#include "lib.h"
static const unsigned char table[256] = {1, 2, 3, 5, 8, 13};
u32 hot(u32 x) { return helper(x) | (x << 1); }
int cold(int a, int b) { return a > b ? a : b; }
int reads_table(unsigned char c) { return table[c] * 3; }
int uses_struct(const struct point *p) { return p->x + p->y; }
int never_called(int a) { return a * 7; }
"""
MAIN_C = """
#include <stdio.h>
#include "lib.h"
int main(int argc, char **argv) {
    u32 acc = 0;
    for (u32 i = 0; i < 1000; i++) acc += hot(i + (u32)argc);
    struct point p = {acc, 1};
    printf("%u %d %d %d\\n", acc, cold(argc, 2), reads_table((unsigned char)argc), uses_struct(&p));
    return 0;
}
"""


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "lib.h").write_text(LIB_H)
    (tmp_path / "lib.c").write_text(LIB_C)
    (tmp_path / "main.c").write_text(MAIN_C)
    (tmp_path / "oqed.toml").write_text('build = "$CC -O2 -o app main.c lib.c"\nrun = "./app"\n')
    return project.load(tmp_path)


def test_scan_finds_ranks_and_slices(proj):
    sc = project.scan(proj)
    by = {f.name: f for f in sc.functions}
    assert set(by) == {"helper", "hot", "cold", "reads_table", "uses_struct", "never_called", "main"}
    # Counted through inlining: hot and its inlined helper run 1000 times each.
    assert by["hot"].calls == 1000 and by["helper"].calls == 1000 and by["cold"].calls == 1
    assert [f.name for f in sc.targets] == ["helper", "hot", "cold", "never_called"]
    assert by["uses_struct"].skipped.startswith("not integers in registers")
    assert by["reads_table"].skipped.startswith("reads global data")
    assert by["main"].skipped == "main"
    assert by["hot"].location == "lib.c:4"

    t = load_target(by["hot"].target)
    assert (t.function, t.args, t.ret, t.loop_free) == ("hot", ["u32"], "u32", True)
    # The slice carries the typedef, the enum and the helper, and makes `hot` external.
    src = t.c_source
    assert "typedef unsigned int u32;" in src and "SHIFT = 3" in src and "static inline u32 helper" in src
    assert "\nu32 hot(u32 x)" in src


def test_unsafe_lock_entry_is_applied_only_when_testing_passes(tmp_path):
    src = """
#include <stdio.h>
static int pc(unsigned x) { int n = 0; while (x) { n += (x >> 1) & 1; x >>= 2; } return n; }
int main(int argc, char **argv) { printf("%d %d\\n", pc(0xF0F0u + argc), pc(argc)); return 0; }
"""
    (tmp_path / "prog.c").write_text(src)

    def build(asm: str, status: str) -> str:
        entry = lock.LockEntry(
            function="pc", args=["u32"], ret="i32", march="x86-64-v3", asm=asm, status=status,
            speedup=1.0, benchmark="pc", run="test", candidate="test",
        )  # fmt: skip
        (tmp_path / "oqed.lock").write_text(lock.dumps([entry]))
        env = {**os.environ, "OQED_CACHE": str(tmp_path / "cache")}
        proc = subprocess.run(
            [sys.executable, "-m", "oqed.cc", "-O2", "-march=x86-64-v3", "-o", "prog", "prog.c"],
            cwd=tmp_path, env=env, capture_output=True, text=True, check=True,
        )  # fmt: skip
        return proc.stderr

    # Counts the odd-position bits; the compilers keep the loop.
    good = "\tand edi, 0xaaaaaaaa\n\tpopcnt eax, edi\n\tret\n"
    wrong = "\tand edi, 0xaaaaaaaa\n\tpopcnt eax, edi\n\tand eax, 7\n\tret\n"
    # The reference loops, so the proof is inconclusive: a proven entry is not trusted...
    assert "pc: kept C (proof unknown" in build(good, "proven")
    # ...a tested entry is substituted when differential testing passes...
    assert "pc: UNSAFE: substituted on testing only" in build(good, "tested")
    out = subprocess.run([str(tmp_path / "prog")], capture_output=True, text=True, check=True).stdout
    assert out.split() == ["4", "0"]
    # ...and never when it fails.
    err = build(wrong, "tested")
    assert "pc: kept C" in err and "testing failed" in err
