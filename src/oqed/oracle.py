"""The project oracle: judge a candidate by building the project with it.

Functions that take pointers, return nothing or read global data can't be run
in the harness on random inputs. For those the project is the test:

1. A few copies of the project are made in a temporary directory, so
   candidates build in parallel without touching the real tree.
2. A candidate is written into a copy's `oqed.lock` (next to the project's
   existing entries) and the project's `build` command runs with `CC` set to
   oqed-cc in trust mode, which substitutes without proving and logs what it
   applied. A candidate that doesn't build, or isn't substituted, fails.
3. The project's `test` command (by default `run`) must print exactly what
   the C build printed, and every file it writes must have exactly the
   contents the C build's run wrote. A crash, a timeout or any difference
   fails. (A round trip alone is a weak test: a `longest_match` that finds
   shorter matches still decompresses to the input, but writes a different,
   larger .gz.) Output that differs between two runs of the C build itself
   (timestamps, timings) is not compared: then only the exit status, or only
   the files that don't vary.
4. The `bench` command (one number) runs `bench_runs` times, alternating with
   a baseline build so both see the same machine state. Without a `bench`,
   the CPU time of `run` is the number. Nothing else oqed runs (other
   candidates' builds and tests) runs during a benchmark (`BenchLock`), and a
   candidate is faster only if it wins enough of the paired runs that chance
   is unlikely (`wins_needed`: 6 of 7, a one-sided sign test at p < 0.07).

Passing says the project's tests can't tell the candidate from the C. It is
not a proof: lock entries found this way are `tested` and need `--unsafe`.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import re
import shutil
import statistics
import subprocess
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from . import lock
from .events import Cycles
from .project import Project, cc_env

TEST_TIMEOUT_S = 120
SIGN_TEST_P = 0.07


def wins_needed(runs: int) -> int:
    """Paired runs a candidate must win so that a tie (each run a coin flip)
    would do as well less than SIGN_TEST_P of the time."""
    for k in range(runs + 1):
        if sum(math.comb(runs, i) for i in range(k, runs + 1)) / 2**runs <= SIGN_TEST_P:
            return k
    return runs + 1  # too few runs to tell: never faster


def run_test(command: str, tree: Path) -> tuple[subprocess.CompletedProcess, dict[str, str]]:
    """Run the test command; also the hash of every file it created or changed."""
    before = _mtimes(tree)
    out = _run(command, tree, timeout=TEST_TIMEOUT_S)
    written = {}
    for rel, m in _mtimes(tree).items():
        if before.get(rel) != m:
            written[rel] = hashlib.sha256((tree / rel).read_bytes()).hexdigest()
    return out, written


def _mtimes(tree: Path) -> dict[str, int]:
    out = {}
    for dirpath, dirs, files in os.walk(tree):
        dirs[:] = [d for d in dirs if d not in (".oqed", ".git")]
        for f in files:
            p = Path(dirpath) / f
            try:
                out[str(p.relative_to(tree))] = p.stat().st_mtime_ns
            except OSError:
                continue
    return out


def differing_file(expected: dict[str, str], written: dict[str, str]) -> str | None:
    """The first file the C build's test wrote that this one didn't write the same."""
    return next((f for f, h in sorted(expected.items()) if written.get(f) != h), None)


class BenchLock:
    """Benchmarks run one at a time and alone: while one runs or waits, no
    build or test starts (they'd compete for the cores, caches and clock).
    Builds and tests share. `async with lock:` takes it for a benchmark."""

    def __init__(self) -> None:
        self._cond = asyncio.Condition()
        self._benching = False
        self._waiting = 0
        self._busy = 0

    async def __aenter__(self) -> None:
        async with self._cond:
            self._waiting += 1
            await self._cond.wait_for(lambda: not self._benching and self._busy == 0)
            self._waiting -= 1
            self._benching = True

    async def __aexit__(self, *exc) -> None:
        async with self._cond:
            self._benching = False
            self._cond.notify_all()

    @asynccontextmanager
    async def shared(self):
        async with self._cond:
            await self._cond.wait_for(lambda: not self._benching and self._waiting == 0)
            self._busy += 1
        try:
            yield
        finally:
            async with self._cond:
                self._busy -= 1
                self._cond.notify_all()


_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


@dataclass
class Verdict:
    ok: bool
    reason: str = ""
    stage: str = ""  # "inline" (build or substitution) or "test"


def _run(cmd: str, cwd: Path, env: dict | None = None, timeout: float | None = None):
    import os

    return subprocess.run(
        cmd,
        shell=True,
        cwd=cwd,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


class ProjectOracle:
    def __init__(self, project: Project, workers: int = 3):
        self.project = project
        lock_path = project.root / lock.LOCK_NAME
        self.entries = lock.load(lock_path) if lock_path.exists() else []
        self.tmp = Path(tempfile.mkdtemp(prefix="oqed-oracle-"))
        ignore = shutil.ignore_patterns(".oqed", ".git")
        self.base = self.tmp / "base"
        shutil.copytree(project.root, self.base, ignore=ignore)
        self._trees: asyncio.Queue[Path] = asyncio.Queue()
        for i in range(workers):
            tree = self.tmp / f"w{i}"
            shutil.copytree(project.root, tree, ignore=ignore)
            self._trees.put_nowait(tree)
        self.expected: str | None = None
        self.exit_only = False  # the test's output varies between runs
        self.expected_files: dict[str, str] = {}  # files the test writes, the same every run

    def close(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build(
        self, tree: Path, entries: list[lock.LockEntry]
    ) -> tuple[subprocess.CompletedProcess, set[str]]:
        (tree / lock.LOCK_NAME).write_text(lock.dumps(entries))
        applied = tree / ".oqed-applied"
        applied.write_text("")
        env = {
            **cc_env(self.project),
            "OQED_LOCK": str(tree / lock.LOCK_NAME),
            "OQED_TRUST": "1",
            "OQED_APPLIED": str(applied),
        }
        proc = _run(self.project.build, tree, env)
        return proc, set(applied.read_text().split())

    def setup(self) -> None:
        """Build the baseline (the C plus the existing lock) and record what its tests print."""
        proc, _ = self._build(self.base, self.entries)
        if proc.returncode != 0:
            raise RuntimeError(f"baseline build failed:\n{(proc.stdout + proc.stderr)[-2000:]}")
        test = self.project.test_command
        (a, fa), (b, fb) = (run_test(test, self.base) for _ in range(2))
        if a.returncode or b.returncode:
            raise RuntimeError(f"`{test}` fails on the baseline build:\n{a.stderr[-2000:]}")
        self.exit_only = a.stdout != b.stdout
        self.expected = a.stdout
        self.expected_files = {f: h for f, h in fa.items() if fb.get(f) == h}

    def _check(self, tree: Path, entry: lock.LockEntry) -> Verdict:
        entries = [e for e in self.entries if e.key != entry.key] + [entry]
        proc, applied = self._build(tree, entries)
        if proc.returncode != 0:
            err = [ln for ln in (proc.stdout + proc.stderr).splitlines() if "error" in ln.lower()]
            return Verdict(False, "does not build: " + " | ".join(err[:3])[:400], "inline")
        if entry.function not in applied:
            fn = f"{entry.function}:"
            why = [ln for ln in proc.stderr.splitlines() if fn in ln or "internal error" in ln]
            return Verdict(False, "not substituted: " + (why[0] if why else "function not found"), "inline")
        try:
            out, written = run_test(self.project.test_command, tree)
        except subprocess.TimeoutExpired:
            return Verdict(False, f"project test timed out after {TEST_TIMEOUT_S}s", "test")
        if out.returncode != 0:
            return Verdict(
                False, f"project test exited with {out.returncode}: {out.stderr.strip()[-300:]}", "test"
            )
        if not self.exit_only and out.stdout != self.expected:
            got, want = out.stdout.splitlines(), (self.expected or "").splitlines()
            i = next(
                (i for i, (a, b) in enumerate(zip(got, want, strict=False)) if a != b),
                min(len(got), len(want)),
            )
            line = lambda xs: xs[i] if i < len(xs) else "<end of output>"  # noqa: E731
            return Verdict(
                False,
                f"project test output differs at line {i + 1}: {line(got)!r}, C gives {line(want)!r}",
                "test",
            )
        if f := differing_file(self.expected_files, written):
            what = (
                "did not write the file the C build writes,"
                if f not in written
                else "wrote different contents than the C build to"
            )
            return Verdict(False, f"project test {what} {f}", "test")
        return Verdict(True)

    def _number(self, tree: Path) -> float:
        return measure(self.project, tree)

    def _bench(self, tree: Path) -> tuple[Cycles, Cycles, int]:
        cand, base = [], []
        for _ in range(self.project.bench_runs):
            base.append(self._number(self.base))
            cand.append(self._number(tree))
        wins = sum(c < b for c, b in zip(cand, base, strict=True))
        return _cycles(cand), _cycles(base), wins

    async def baseline(self, bench_lock: BenchLock) -> Cycles:
        async with bench_lock:
            return _cycles(
                [await asyncio.to_thread(self._number, self.base) for _ in range(self.project.bench_runs)]
            )

    async def evaluate(
        self, entry: lock.LockEntry, bench_lock: BenchLock
    ) -> tuple[Verdict, Cycles | None, Cycles | None, bool]:
        """Build and test the candidate; when it passes, time it against the
        baseline. Returns the verdict, the candidate's and the baseline's
        numbers, and whether it won enough paired runs to count as faster."""
        tree = await self._trees.get()
        try:
            async with bench_lock.shared():
                v = await asyncio.to_thread(self._check, tree, entry)
            if not v.ok:
                return v, None, None, False
            async with bench_lock:
                cand, base, wins = await asyncio.to_thread(self._bench, tree)
            return v, cand, base, wins >= wins_needed(self.project.bench_runs)
        finally:
            self._trees.put_nowait(tree)


def measure(p: Project, tree: Path) -> float:
    """One benchmark measurement of the build in `tree`; lower is better."""
    if p.bench is None:
        return cpu_ms(p.run, tree)
    out = _run(p.bench, tree, timeout=TEST_TIMEOUT_S)
    lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
    nums = _NUMBER.findall(lines[-1]) if lines and out.returncode == 0 else []
    if not nums:
        raise RuntimeError(f"`{p.bench}` printed no number (exit {out.returncode})")
    x = float(nums[-1])
    return 1 / x if p.bench_higher_is_better else x


def cpu_ms(command: str, cwd: Path) -> float:
    """User + system CPU time of a shell command and everything it waits for,
    in milliseconds (steadier than wall time on a busy machine)."""
    import os

    proc = subprocess.Popen(
        command, shell=True, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    _, status, ru = os.wait4(proc.pid, 0)
    proc.returncode = os.waitstatus_to_exitcode(status)
    if proc.returncode != 0:
        raise RuntimeError(f"`{command}` exited with {proc.returncode}")
    return (ru.ru_utime + ru.ru_stime) * 1000


def _cycles(xs: list[float]) -> Cycles:
    if len(xs) < 4:
        return Cycles(median=round(statistics.median(xs), 3), spread=round(max(xs) - min(xs), 3))
    q1, med, q3 = statistics.quantiles(xs, n=4)
    return Cycles(median=round(med, 3), spread=round(q3 - q1, 3))
