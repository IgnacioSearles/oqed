"""`oqed report`: what the lock does to the whole project, measured.

Two copies of the project are built, one with the plain compiler and one
with oqed-cc applying `oqed.lock` exactly as a user's build would (proofs,
source hashes, the vectorization guard; not the oracle's trust mode). Then:

- the test command (by default `run`) must give the same result on both:
  exit status, output and the contents of every file it writes;
- the benchmark (by default `run`'s CPU time) runs `runs` times on each,
  alternating, and the medians and spreads are reported.

The result goes to `.oqed/report.json` (the sweep table and the UI read it)
and is printed. Numbers are what was measured on this machine, small or not.
"""

from __future__ import annotations

import json
import shutil
import statistics
import tempfile
from pathlib import Path

from . import lock
from .oracle import _run, differing_file, measure, run_test
from .project import Project, cc_env, plain_env


def _quartiles(xs: list[float]) -> dict:
    q1, med, q3 = statistics.quantiles(xs, n=4) if len(xs) >= 4 else (min(xs), statistics.median(xs), max(xs))
    return {
        "median": round(med, 4),
        "q1": round(q1, 4),
        "q3": round(q3, 4),
        "runs": [round(x, 4) for x in xs],
    }


def report(project: Project, runs: int = 11, log=print) -> dict:
    lock_path = project.root / lock.LOCK_NAME
    entries = lock.load(lock_path) if lock_path.exists() else []
    tmp = Path(tempfile.mkdtemp(prefix="oqed-report-"))
    try:
        ignore = shutil.ignore_patterns(".oqed", ".git")
        plain, tuned = tmp / "plain", tmp / "oqed"
        shutil.copytree(project.root, plain, ignore=ignore)
        shutil.copytree(project.root, tuned, ignore=ignore)
        (plain / lock.LOCK_NAME).unlink(missing_ok=True)

        log(f"[report] building plain ({project.cc}) and with oqed-cc ({len(entries)} lock entries)")
        b = _run(project.build, plain, plain_env(project))
        if b.returncode != 0:
            raise RuntimeError(f"plain build failed:\n{(b.stdout + b.stderr)[-2000:]}")
        env = {**cc_env(project), "OQED_LOCK": str(tuned / lock.LOCK_NAME)}
        env["OQED_CACHE"] = str(project.state / "cc-cache")
        b = _run(project.build, tuned, env)
        if b.returncode != 0:
            raise RuntimeError(f"oqed-cc build failed:\n{(b.stdout + b.stderr)[-2000:]}")
        notes = sorted(
            {ln.removeprefix("oqed-cc: ") for ln in b.stderr.splitlines() if ln.startswith("oqed-cc:")}
        )
        applied = [n for n in notes if ": substituted" in n or ": UNSAFE" in n]
        for n in notes:
            log(f"  {n[:160]}")

        test = project.test_command
        (a, fa), (a2, fa2), (c, fc) = (run_test(test, t) for t in (plain, plain, tuned))
        same_exit = a.returncode == c.returncode == 0
        same_output = a.stdout == c.stdout
        if not same_exit:
            raise RuntimeError(f"`{test}` fails: plain exit {a.returncode}, oqed exit {c.returncode}")
        stable = {f: h for f, h in fa.items() if fa2.get(f) == h}  # files that don't vary run to run
        bad_file = differing_file(stable, fc)
        log(
            f"[report] `{test}`: both exit 0, output {'identical' if same_output else 'DIFFERENT'}, "
            f"{len(stable)} output files " + (f"DIFFERENT ({bad_file})" if bad_file else "identical")
        )

        base, cand = [], []
        for _i in range(runs):
            base.append(measure(project, plain))
            cand.append(measure(project, tuned))
        b_q, c_q = _quartiles(base), _quartiles(cand)
        wins = sum(c < b for c, b in zip(cand, base, strict=True))
        speedup = round(b_q["median"] / c_q["median"], 3)
        result = {
            "project": project.root.name,
            "compiler": project.cc,
            "unit": project.unit,
            "bench": project.bench or project.run,
            "entries": len(entries),
            "proven": sum(e.status == "proven" for e in entries),
            "tested": sum(e.status == "tested" for e in entries),
            "applied": len(applied),
            "notes": notes,
            "test": test,
            "test_output_identical": same_output,
            "output_files": len(stable),
            "output_files_identical": bad_file is None,
            "plain": b_q,
            "oqed": c_q,
            "speedup": speedup,
            # Only an oqed build that behaves exactly like the plain one counts.
            "valid": same_output and bad_file is None,
            "faster_runs": wins,  # of `runs` alternating pairs
        }
        project.state.mkdir(exist_ok=True)
        (project.state / "report.json").write_text(json.dumps(result, indent=2) + "\n")
        log(
            f"[report] {project.root.name}: plain {b_q['median']:.4g} {project.unit} "
            f"(IQR {b_q['q3'] - b_q['q1']:.2g}), oqed {c_q['median']:.4g} (IQR {c_q['q3'] - c_q['q1']:.2g}): "
            f"{speedup}x, faster in {wins} of {runs} paired runs, "
            f"{len(applied)} of {len(entries)} entries applied"
            + (
                ""
                if result["valid"]
                else "; NOT VALID: the oqed build's output differs, so this doesn't count"
            )
        )
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
