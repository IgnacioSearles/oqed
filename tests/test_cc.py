import os
import subprocess
import sys
from pathlib import Path

import pytest

from oqed import cc, lock

HIGH_BIT_C = """
static int high_bit(unsigned int z) {
   int n=0;
   if (z == 0) return %s;
   if (z >= 0x10000) { n += 16; z >>= 16; }
   if (z >= 0x00100) { n +=  8; z >>=  8; }
   if (z >= 0x00010) { n +=  4; z >>=  4; }
   if (z >= 0x00004) { n +=  2; z >>=  2; }
   if (z >= 0x00002) { n +=  1; }
   return n;
}
"""
MAIN = """
#include <stdio.h>
int main(int argc, char **argv) {
    unsigned v[] = {0, 1, 2, 3, 0x80000000u, 12345};
    for (int i = 0; i < 6; i++) printf("%d\\n", high_bit(v[i] + (unsigned)argc - 1));
    return 0;
}
"""
LOOP = """
void all(const unsigned *in, int *out, int n) {
    for (int i = 0; i < n; i++) out[i] = high_bit(in[i]);
}
"""
ENTRY = lock.LockEntry(
    function="high_bit",
    args=["u32"],
    ret="i32",
    march="x86-64-v3",
    asm="\tmov eax, 31\n\tlzcnt ecx, edi\n\tsub eax, ecx\n\tret\n",
    status="proven",
    speedup=4.3,
    benchmark="stbi_high_bit",
    run="test",
    candidate="test",
)


def build(tmp_path: Path, source: str, *flags: str, compiler: str = "clang") -> tuple[Path, str]:
    (tmp_path / "oqed.lock").write_text(lock.dumps([ENTRY]))
    (tmp_path / "prog.c").write_text(source)
    exe = tmp_path / "prog"
    env = {**os.environ, "OQED_CACHE": str(tmp_path / "cache"), "OQED_CC": compiler}
    proc = subprocess.run(
        [sys.executable, "-m", "oqed.cc", "-O2", *flags, "-o", str(exe), "prog.c"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return exe, proc.stderr


def disasm(exe: Path) -> str:
    return subprocess.run(["objdump", "-d", str(exe)], capture_output=True, text=True, check=True).stdout


@pytest.mark.parametrize("compiler", ["clang", "gcc"])
def test_substitutes_proven_entry(tmp_path, compiler):
    exe, err = build(tmp_path, HIGH_BIT_C % "-1" + MAIN, "-march=x86-64-v3", compiler=compiler)
    assert "high_bit: substituted (proven equivalent" in err
    assert "lzcnt" in disasm(exe)
    out = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout
    assert out.split() == ["-1", "0", "1", "1", "31", "13"]
    # A rebuild of the same source reuses the cached decision.
    assert any((tmp_path / "cache" / "cc").iterdir())
    _, err2 = build(tmp_path, HIGH_BIT_C % "-1" + MAIN, "-march=x86-64-v3", compiler=compiler)
    assert "substituted" in err2


def test_stale_entry_is_caught_by_the_proof(tmp_path):
    # The source changed what zero maps to; the lock entry still returns -1.
    exe, err = build(tmp_path, HIGH_BIT_C % "-2" + MAIN, "-march=x86-64-v3")
    assert "high_bit: kept C (proof not_equivalent" in err
    assert "lzcnt" not in disasm(exe)


def test_older_march_keeps_c(tmp_path):
    _, err = build(tmp_path, HIGH_BIT_C % "-1" + MAIN, "-march=x86-64-v2")
    assert "kept C (needs lzcnt" in err


def test_vectorization_guard(tmp_path):
    # clang vectorizes the C loop; an asm block would make it scalar.
    _, err = build(tmp_path, HIGH_BIT_C % "-1" + LOOP + MAIN, "-O3", "-march=x86-64-v3")
    assert "high_bit: kept C (a loop would stop vectorizing at prog.c:" in err


def test_commands_left_alone():
    assert cc.parse(["-o", "prog", "a.o", "b.o", "-lm"]) is None
    assert cc.parse(["-E", "a.c"]) is None
    assert cc.parse(["-flto", "-c", "a.c"]) is None
    cmd = cc.parse(["-O2", "-march=x86-64-v3", "-DX=1", "-I", "inc", "-c", "a.c", "-o", "a.o", "-MD"])
    assert cmd is not None and cmd.sources == [6] and cmd.march == "x86-64-v3"
    assert cmd.flags == ["-O2", "-march=x86-64-v3", "-DX=1", "-I", "inc"]


def test_no_lock_compiles_unchanged(tmp_path, monkeypatch):
    (tmp_path / "prog.c").write_text(HIGH_BIT_C % "-1" + MAIN)
    monkeypatch.delenv("OQED_LOCK", raising=False)
    proc = subprocess.run(
        [sys.executable, "-m", "oqed.cc", "-O2", "-o", "prog", "prog.c"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0 and proc.stderr == ""
