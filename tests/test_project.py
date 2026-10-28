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
int uses_struct(const struct point *p) { return p->x * 3 + p->y * 5; }
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
    assert [f.name for f in sc.targets] == ["hot", "cold", "reads_table", "uses_struct"]
    # One or two instructions from both compilers: nothing to gain.
    assert by["helper"].skipped.startswith("already minimal")
    assert by["never_called"].skipped.startswith("already minimal")
    # Pointers and global data need the project oracle (`run` is the test and the benchmark).
    assert by["uses_struct"].oracle == by["reads_table"].oracle == "project"
    assert by["hot"].oracle == "harness"
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


ORACLE_LIB = """
#include <assert.h>
#include <stdint.h>
uint32_t load_le32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}
__attribute__((noinline)) static uint32_t mix(uint32_t x) { return x * 0x9e3779b1u ^ (x >> 15); }
uint32_t sum_mixed(const uint32_t *p, int n) {
    uint32_t s = 0;
    assert(n >= 0);  /* puts the file's path in the code: the slice hash must not depend on it */
    for (int i = 0; i < n; i++) s += mix(p[i]);
    return s;
}
"""
ORACLE_MAIN = """
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
uint32_t load_le32(const uint8_t *p);
uint32_t sum_mixed(const uint32_t *p, int n);
int main(int argc, char **argv) {
    static uint8_t buf[4096];
    for (int i = 0; i < 4096; i++) buf[i] = (uint8_t)(i * 37 + 11);
    struct timespec a, b;
    clock_gettime(CLOCK_MONOTONIC, &a);
    uint32_t h = 0;
    for (int r = 0; r < 200; r++)
        for (int i = 0; i + 4 <= 4096; i++) h = h * 31 + load_le32(buf + i);
    clock_gettime(CLOCK_MONOTONIC, &b);
    if (argc > 1 && strcmp(argv[1], "--time") == 0)
        printf("%.1f\\n", (b.tv_sec - a.tv_sec) * 1e9 + (b.tv_nsec - a.tv_nsec));
    else
        printf("%08x %08x\\n", h, sum_mixed((const uint32_t *)buf, 1000 + argc));
    return 0;
}
"""


@pytest.fixture
def oracle_proj(tmp_path):
    (tmp_path / "lib.c").write_text(ORACLE_LIB)
    (tmp_path / "main.c").write_text(ORACLE_MAIN)
    (tmp_path / "oqed.toml").write_text(
        'build = "$CC -O2 -march=x86-64-v3 -o app main.c lib.c"\nrun = "./app"\n'
        'test = "./app"\nbench = "./app --time"\nbench_runs = 3\n'
    )
    return project.load(tmp_path)


def test_project_oracle_judges_memory_candidates(oracle_proj, tmp_path, monkeypatch):
    monkeypatch.setattr(project, "MINIMAL", 0)  # its functions are tiny on purpose
    import asyncio

    from oqed.events import read_events
    from oqed.providers import MockProvider
    from oqed.search import SearchConfig, run_search

    sc = project.scan(oracle_proj)
    f = next(f for f in sc.functions if f.name == "load_le32")
    assert f.oracle == "project" and f.calls == 200 * 4093
    t = load_target(f.target)
    answers = {
        "good": "mov eax, dword ptr [rdi]\nret",
        "wrong": "movzx eax, word ptr [rdi]\nret",
        "stack": "push rbx\nmov eax, dword ptr [rdi]\npop rbx\nret",
        "label": "mov eax, dword ptr [rip + .LCPI0_0]\nret",
        "missing": "mov eax, dword ptr [rip + oqed_no_such_symbol]\nret",
    }
    replies = [f"<explanation>{k}</explanation><asm>\n{v}\n</asm>" for k, v in answers.items()]
    mock = MockProvider(replies, seed=3)
    cfg = SearchConfig(waves=False, candidates=40, concurrency=1, stall_after=100)
    run_id = asyncio.run(run_search([t], mock, mock, cfg, runs_dir=tmp_path / "runs"))
    events = read_events(tmp_path / "runs" / f"{run_id}.jsonl")
    kind = {e.candidate_id: e.explanation for e in events if e.type == "candidate_proposed"}
    outcome = {}
    for e in events:
        if e.type == "candidate_rejected" and e.stage != "duplicate":
            outcome[kind[e.candidate_id]] = (e.stage, e.reason)
        elif e.type == "candidate_benchmarked":
            outcome[kind[e.candidate_id]] = ("passed", "")
    assert set(outcome) == set(answers), outcome
    assert outcome["good"][0] in ("passed", "benchmark")
    assert outcome["wrong"][0] == "test" and "output differs" in outcome["wrong"][1]
    # Uses the stack, so it can't be inline asm: it becomes the whole (naked) function.
    assert outcome["stack"][0] in ("passed", "benchmark"), outcome["stack"]
    # Compiler-only labels and unknown symbols don't exist in the project build.
    for k in ("label", "missing"):
        assert outcome[k][0] == "inline" and "does not build" in outcome[k][1], outcome[k]


def test_project_entry_applies_only_to_the_tested_source(oracle_proj, monkeypatch):
    monkeypatch.setattr(project, "MINIMAL", 0)  # its functions are tiny on purpose
    sc = project.scan(oracle_proj)
    t = load_target(next(f for f in sc.functions if f.name == "load_le32").target)
    root = oracle_proj.root
    entry = lock.LockEntry(
        function="load_le32", args=t.args, ret=t.ret, march="x86-64-v3",
        asm="\tmov eax, dword ptr [rdi]\n\tret\n", status="tested", speedup=1.0, benchmark=t.name,
        run="test", candidate="test", oracle="project", slice_sha=t.slice_sha,
    )  # fmt: skip
    (root / "oqed.lock").write_text(lock.dumps([entry]))

    def build() -> str:
        env = {**os.environ, "OQED_CACHE": str(root / "cache")}
        cmd = [sys.executable, "-m", "oqed.cc", "-O2", "-march=x86-64-v3", "-o", "app", "main.c", "lib.c"]
        return subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True, check=True).stderr

    assert "load_le32: UNSAFE: substituted on testing only (tested with the project's test command" in build()
    out = subprocess.run(["./app"], cwd=root, capture_output=True, text=True, check=True).stdout
    (root / "lib.c").write_text(ORACLE_LIB.replace("p[3] << 24", "p[3] << 24 | 0"))
    assert "load_le32: kept C (source changed since it was tested)" in build()
    assert subprocess.run(["./app"], cwd=root, capture_output=True, text=True, check=True).stdout == out


# sum_mixed in asm: a loop calling the static helper `mix`, saving the
# registers it keeps across calls (three pushes also realign the stack).
# Numeric labels: `1b` is a binary number to clang's Intel-syntax parser.
SUM_MIXED = """\tpush rbx
\tpush r12
\tpush r13
\tmov r12, rdi
\tmov r13d, esi
\txor ebx, ebx
\ttest r13d, r13d
\tjle 2f
1:
\tmov edi, dword ptr [r12]
\tcall mix
\tadd ebx, eax
\tadd r12, 4
\tdec r13d
\tjnz 1b
2:
\tmov eax, ebx
\tpop r13
\tpop r12
\tpop rbx
\tret
"""


@pytest.mark.parametrize("cc", ["clang", "gcc"])
def test_functions_that_call_others_are_substituted_whole(oracle_proj, cc):
    sc = project.scan(oracle_proj)
    f = next(f for f in sc.functions if f.name == "sum_mixed")
    assert f.oracle == "project", f.skipped
    t = load_target(f.target)
    root = oracle_proj.root
    entry = lock.LockEntry(
        function="sum_mixed", args=t.args, ret=t.ret, march="x86-64-v3", asm=SUM_MIXED,
        status="tested", speedup=1.0, benchmark=t.name, run="test", candidate="test",
        oracle="project", slice_sha=t.slice_sha,
    )  # fmt: skip
    files = ["main.c", "lib.c"]

    def build(*flags: str) -> str:
        env = {**os.environ, "OQED_CACHE": str(root / "cache"), "OQED_LOCK": str(root / "oqed.lock")}
        cmd = [sys.executable, "-m", "oqed.cc", f"--cc={cc}", "-O3", "-march=x86-64-v3", *flags]
        return subprocess.run(
            cmd + ["-o", "app", *files], cwd=root, env=env, capture_output=True, text=True, check=True
        ).stderr

    run = lambda: subprocess.run(["./app"], cwd=root, capture_output=True, text=True, check=True).stdout  # noqa: E731
    build()
    want = run()
    (root / "oqed.lock").write_text(lock.dumps([entry]))
    for flags in ((), ("-masm=intel",)):
        assert "sum_mixed: UNSAFE: substituted on testing only" in build(*flags)
        assert run() == want
        # The compiled program calls the asm, and the asm calls mix.
        objdump = ["objdump", "-d", "-M", "intel", "app"]
        dis = subprocess.run(objdump, cwd=root, capture_output=True, text=True, check=True)
        body = dis.stdout.split("<sum_mixed>:")[1].split("\n\n")[0]
        assert "call" in body and "<mix" in body


def test_build_and_run_are_enough(oracle_proj, monkeypatch):
    # No test and no bench: `run` is the test (its output has a timestamp, so
    # only its exit status counts) and its CPU time is the benchmark.
    monkeypatch.setattr(project, "MINIMAL", 0)
    import asyncio

    from oqed.oracle import ProjectOracle

    root = oracle_proj.root
    (root / "oqed.toml").write_text(
        'build = "$CC -O2 -march=x86-64-v3 -o app main.c lib.c"\nrun = "./app --time"\nbench_runs = 3\n'
    )
    proj = project.load(root)
    t = load_target(next(f for f in project.scan(proj).functions if f.name == "load_le32").target)
    o = ProjectOracle(proj, workers=1)
    try:
        o.setup()
        assert o.exit_only
        assert proj.unit == "CPU ms"

        def entry(asm):
            return lock.LockEntry(
                function="load_le32", args=t.args, ret=t.ret, march="x86-64-v3", asm=asm,
                status="tested", speedup=1.0, benchmark=t.name, run="test", candidate="test",
                oracle="project", slice_sha=t.slice_sha,
            )  # fmt: skip

        async def judge(asm):
            return await o.evaluate(entry(asm), asyncio.Lock())

        v, cyc, base = asyncio.run(judge("\tmov eax, dword ptr [rdi]\n\tret\n"))
        assert v.ok and cyc.median > 0 and base.median > 0
        v, _, _ = asyncio.run(judge("\txor eax, eax\n\tmov eax, dword ptr [rax]\n\tret\n"))
        assert not v.ok and v.stage == "test" and "exited with" in v.reason
    finally:
        o.close()
