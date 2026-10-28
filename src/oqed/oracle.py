"""The project oracle: judge a candidate by building the project with it.

Functions that take pointers, return nothing or read global data can't be run
in the harness on random inputs. For those the project is the test:

1. A few copies of the project are made in a temporary directory, so
   candidates build in parallel without touching the real tree.
2. A candidate is written into a copy's `oqed.lock` (next to the project's
   existing entries) and the project's `build` command runs with `CC` set to
   oqed-cc in trust mode, which substitutes without proving and logs what it
   applied. A candidate that doesn't build, or isn't substituted, fails.
3. The project's `test` command must print exactly what the C build printed.
   A crash, a timeout or any difference fails.
4. The `bench` command (one number, lower is better) runs `bench_runs` times,
   alternating with a baseline build so both see the same machine state.

Passing says the project's tests can't tell the candidate from the C. It is
not a proof: lock entries found this way are `tested` and need `--unsafe`.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import statistics
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import lock
from .events import Cycles
from .project import Project, _oqed_cc

TEST_TIMEOUT_S = 120
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
        if not (project.test and project.bench):
            raise ValueError("the project oracle needs `test` and `bench` in oqed.toml")
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

    def close(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build(
        self, tree: Path, entries: list[lock.LockEntry]
    ) -> tuple[subprocess.CompletedProcess, set[str]]:
        (tree / lock.LOCK_NAME).write_text(lock.dumps(entries))
        applied = tree / ".oqed-applied"
        applied.write_text("")
        env = {
            "CC": f"{_oqed_cc()} --cc={self.project.cc}",
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
        outs = [_run(self.project.test, self.base, timeout=TEST_TIMEOUT_S) for _ in range(2)]
        if any(o.returncode != 0 for o in outs):
            raise RuntimeError(
                f"`{self.project.test}` fails on the baseline build:\n{outs[0].stderr[-2000:]}"
            )
        if outs[0].stdout != outs[1].stdout:
            raise RuntimeError(
                f"`{self.project.test}` prints different output on each run; it can't be the oracle"
            )
        self.expected = outs[0].stdout

    def _check(self, tree: Path, entry: lock.LockEntry) -> Verdict:
        entries = [e for e in self.entries if e.function != entry.function] + [entry]
        proc, applied = self._build(tree, entries)
        if proc.returncode != 0:
            err = [ln for ln in (proc.stdout + proc.stderr).splitlines() if "error" in ln.lower()]
            return Verdict(False, "does not build: " + " | ".join(err[:3])[:400], "inline")
        if entry.function not in applied:
            fn = f"{entry.function}:"
            why = [ln for ln in proc.stderr.splitlines() if fn in ln or "internal error" in ln]
            return Verdict(False, "not substituted: " + (why[0] if why else "function not found"), "inline")
        try:
            out = _run(self.project.test, tree, timeout=TEST_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return Verdict(False, f"project test timed out after {TEST_TIMEOUT_S}s", "test")
        if out.returncode != 0:
            return Verdict(
                False, f"project test exited with {out.returncode}: {out.stderr.strip()[-300:]}", "test"
            )
        if out.stdout != self.expected:
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
        return Verdict(True)

    def _number(self, tree: Path) -> float:
        out = _run(self.project.bench, tree, timeout=TEST_TIMEOUT_S)
        lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
        nums = _NUMBER.findall(lines[-1]) if lines and out.returncode == 0 else []
        if not nums:
            raise RuntimeError(f"`{self.project.bench}` printed no number (exit {out.returncode})")
        return float(nums[-1])

    def _bench(self, tree: Path) -> tuple[Cycles, Cycles]:
        cand, base = [], []
        for _ in range(self.project.bench_runs):
            base.append(self._number(self.base))
            cand.append(self._number(tree))
        return _cycles(cand), _cycles(base)

    async def baseline(self, bench_lock: asyncio.Lock) -> Cycles:
        async with bench_lock:
            return _cycles(
                [await asyncio.to_thread(self._number, self.base) for _ in range(self.project.bench_runs)]
            )

    async def evaluate(
        self, entry: lock.LockEntry, bench_lock: asyncio.Lock
    ) -> tuple[Verdict, Cycles | None, Cycles | None]:
        """Build and test the candidate; when it passes, time it against the
        baseline. Returns the verdict, the candidate's and the baseline's numbers."""
        tree = await self._trees.get()
        try:
            v = await asyncio.to_thread(self._check, tree, entry)
            if not v.ok:
                return v, None, None
            async with bench_lock:
                cand, base = await asyncio.to_thread(self._bench, tree)
            return v, cand, base
        finally:
            self._trees.put_nowait(tree)


def _cycles(xs: list[float]) -> Cycles:
    if len(xs) < 4:
        return Cycles(median=round(statistics.median(xs), 3), spread=round(max(xs) - min(xs), 3))
    q1, med, q3 = statistics.quantiles(xs, n=4)
    return Cycles(median=round(med, 3), spread=round(q3 - q1, 3))
