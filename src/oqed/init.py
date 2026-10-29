"""`oqed init`: write `oqed.toml` for a project nobody configured.

The model reads the repository (file list, README, build files) and proposes
the commands: `build` (rebuild everything with `$CC`), `run` (a workload of a
few seconds that exercises the code, deterministic where possible), and
optionally `test` and `bench`. oqed then checks the proposal by running it:

1. `build` with oqed-cc in record mode must compile C through `$CC` and link,
   and `run` must exit 0 and spend time in the project's code;
2. `run` must take between 0.5 and 5 seconds of CPU time;
3. a second `build` must leave every file the size the first left it;
4. `test`, if given, must pass on its own after a build; `bench`, if given, must print a number.

When a step fails, the error goes back to the model for another proposal, a
few times. Every proposal and check is printed, so a person can see what was
tried and fix `oqed.toml` by hand instead.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path

from . import project as proj
from .oracle import _NUMBER, cpu_ms

# Files that say how a project builds, tests and benchmarks.
_BUILD_FILES = [
    "README.md", "README", "README.rst", "README.txt", "INSTALL", "INSTALL.md", "BUILDING.md",
    "Makefile", "GNUmakefile", "makefile", "CMakeLists.txt", "meson.build", "configure.ac",
    "build.sh", "tests/Makefile", "test/Makefile", "tests/CMakeLists.txt", "programs/Makefile",
    "bench/Makefile", "benchmarks/Makefile",
]  # fmt: skip
_SKIP_DIRS = {".git", ".oqed", "node_modules", "build", "dist", ".github", "doc", "docs"}
MAX_FILES = 300
SUPPORT = proj.SUPPORT
RUN_CPU_MS = (500, 5_000)

SYSTEM = """You configure oqed, a tool that speeds up C projects by replacing hot \
functions with faster assembly at build time. oqed needs to rebuild the project with its \
own compiler wrapper, run a workload that exercises the code, and tell whether the program \
still behaves the same. You write `oqed.toml` for a project you are shown.

Keys (TOML, all values strings unless noted):
- build: shell command, run in the project root, that rebuilds ALL of the project's C and C++ code \
from scratch and links the workload's program. It must compile with the compiler in the \
environment variables $CC and, for C++, $CXX (e.g. `make clean && make CC="$CC" CXX="$CXX" \
CFLAGS="-O3 -march=x86-64-v3" prog`, or for CMake `rm -rf .oqed/build && cmake -S . -B .oqed/build \
-DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX" \
-DCMAKE_BUILD_TYPE=Release && cmake --build .oqed/build -j8`). Use -O3 (or the project's \
release flags) and -march=x86-64-v3. Prefer static linking of the project's own library \
into the program, so the program uses the code just built. Build only what the workload \
needs (one program or test binary), not the whole world: no docs, no install, no shared \
library variants if avoidable. If the workload needs generated input, generate it at the end \
of `build` (inside the project directory), so `run` spends its time in the project's code.
- run: shell command, run in the project root, that runs a workload of about 1 second (0.5 to \
3 seconds of CPU time, nearly all of it in the project's code: no input generation or \
interpreters here) exercising the project's hot code: a test program or the project's \
own tool, on input that exists in the repo or that the command generates deterministically \
(e.g. the repo's own source files concatenated, or `head -c 20000000 /dev/urandom` is NOT \
deterministic so don't). It must do a FIXED AMOUNT OF WORK: oqed times it, so a benchmark \
mode that runs for a fixed time (many `-b` / `--bench` modes do) is useless here; if the \
tool has such a mode, use a plain fixed job instead (e.g. compress and decompress a file a \
set number of times). Several copies of the project run at once, so write files only \
inside the project directory with relative paths, never in /tmp, and not in .oqed/ (oqed's \
own). No network.
- test (optional): a command whose exit status, and ideally output, show the code is \
correct: the project's test suite or a round trip (compress then decompress and compare). \
It runs ON ITS OWN in a fresh copy right after `build` (not after `run`), so it must create \
any input it needs. Omit it to use `run` (then make `run` itself fail when results are wrong, \
e.g. end it with a `cmp`). oqed compares what the test prints and every file it writes with \
the original build's, byte for byte, so make those cover ALL of the program's results: print \
or write the full output, or a hash over every byte of it (e.g. FNV-1a), never a summary like \
a length or a few sample bytes. If you write a driver program, it should hash everything it \
produces and print the hashes.
- bench (optional): a command printing one number on its LAST line measuring speed; omit \
it to let oqed time `run` itself (usually best). If given, set bench_unit, and set \
bench_higher_is_better = true (a TOML boolean) for throughputs like MB/s.
- cc (optional): "clang" (default) or "gcc".

Driver programs and input generators go in files of their own, never inlined in a command \
(quoting C or Python inside a TOML string inside a shell command breaks): give each as \
<file path="oqed_support/NAME">contents</file>, in the directory oqed_support/, and refer to \
it from the commands by that relative path (e.g. `python3 oqed_support/gen.py > input.yaml`, \
`$CC -O3 -march=x86-64-v3 -Iinclude -o bench oqed_support/driver.c libfoo.a`).

Reply with any files, then oqed.toml inside <toml></toml> tags, then nothing else. Every reply \
gives the complete set: files from earlier replies that aren't repeated are deleted."""


def repo_context(root: Path) -> str:
    try:
        files = subprocess.run(
            ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.split()
    except (subprocess.CalledProcessError, FileNotFoundError):
        files = [
            str(p.relative_to(root))
            for p in sorted(root.rglob("*"))
            if p.is_file() and not set(p.relative_to(root).parts) & _SKIP_DIRS
        ]
    files = [f for f in files if not set(Path(f).parts[:-1]) & _SKIP_DIRS]
    listing = "\n".join(files[:MAX_FILES]) + (
        f"\n... ({len(files) - MAX_FILES} more)" if len(files) > MAX_FILES else ""
    )
    parts = [f"Files in the repository:\n{listing}"]
    budget = 24_000
    for name in _BUILD_FILES:
        p = root / name
        if p.is_file() and budget > 0:
            text = p.read_text(errors="replace")[: min(8000, budget)]
            budget -= len(text)
            parts.append(f"--- {name} ---\n{text}")
    return "\n\n".join(parts)


def parse_reply(text: str) -> str:
    text = re.sub(r"<file\b.*?</file>", "", text, flags=re.S)
    m = re.search(r"<toml>(.*?)</toml>", text, re.S) or re.search(r"```(?:toml)?\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip() + "\n"


def parse_files(text: str) -> dict[str, str]:
    """Support files in a reply, by path under oqed_support/."""
    out = {}
    for m in re.finditer(r'<file\s+path="([^"]+)"\s*>\n?(.*?)</file>', text, re.S):
        path = Path(m.group(1))
        if path.is_absolute() or ".." in path.parts:
            continue
        if path.parts[0] != SUPPORT:
            path = Path(SUPPORT, *path.parts)
        out[str(path)] = m.group(2)
    return out


def write_files(root: Path, files: dict[str, str]) -> None:
    shutil.rmtree(root / SUPPORT, ignore_errors=True)
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def _fresh_copy(root: Path, dest: Path) -> None:
    """The project as committed (a git repo: earlier attempts may have left
    generated files behind, and a config must not depend on them), else as is."""
    archive = subprocess.run(["git", "archive", "HEAD"], cwd=root, capture_output=True, check=False)
    if archive.returncode == 0:
        dest.mkdir(parents=True)
        subprocess.run(["tar", "-x", "-C", str(dest)], input=archive.stdout, check=True)
        if (root / SUPPORT).is_dir():
            shutil.copytree(root / SUPPORT, dest / SUPPORT, dirs_exist_ok=True)
    else:
        shutil.copytree(root, dest, ignore=shutil.ignore_patterns(".oqed", ".git"))


def check(root: Path, toml: str, log=print) -> str | None:
    """Run the proposed config; None if it works, else what went wrong."""
    try:
        cfg = tomllib.loads(toml)
    except tomllib.TOMLDecodeError as e:
        return f"not valid TOML: {e}"
    missing = [k for k in ("build", "run") if not isinstance(cfg.get(k), str)]
    if missing:
        return f"missing {', '.join(missing)}"
    if re.search(r"(^|[\s=\"'>])/tmp\b", " ".join(str(cfg.get(k, "")) for k in ("run", "test", "bench"))):
        return "the commands use /tmp; copies of the project run in parallel, so use relative paths"
    if re.search(
        r"(^|[\s=\"'>/])\.oqed(/|\b)",
        " ".join(str(cfg.get(k, "")) for k in ("build", "run", "test", "bench")),
    ):
        return "the commands use .oqed/, which is oqed's own state directory; use another directory"
    (root / proj.CONFIG).write_text(toml)
    p = proj.load(root)
    log(f"  building with oqed-cc (record mode): {p.build}")
    t0 = time.perf_counter()
    try:
        rec = proj.record(p)
    except RuntimeError as e:
        return str(e)
    log(f"  built and ran in {time.perf_counter() - t0:.0f}s")
    shares = proj.read_time(rec / "profile.txt")
    if not shares:
        return (
            "the build compiled through $CC, but `run` spent no time in the code it compiled "
            "(is the program linked against the library just built, statically?)"
        )
    try:
        ms = min(cpu_ms(p.run, root) for _ in range(2))
    except RuntimeError as e:
        return str(e)
    top = ", ".join(f"{name} {100 * t:.0f}%" for name, t in sorted(shares.items(), key=lambda x: -x[1])[:3])
    log(f"  run: {ms:.0f} ms CPU; most time in {top}")
    if not RUN_CPU_MS[0] <= ms <= RUN_CPU_MS[1]:
        return f"`run` takes {ms:.0f} ms of CPU time; it should take about 1 second (0.5 to 3)"

    # The oracle runs `test` alone in a fresh copy of the project, after a
    # build, and rebuilds its copies many times: a second build must leave the
    # tree as the first did (a build that appends to its input file doesn't).
    with tempfile.TemporaryDirectory(prefix="oqed-init-") as d:
        fresh = Path(d) / root.name
        _fresh_copy(root, fresh)
        env = {**os.environ, **proj.plain_env(p)}
        sizes = []
        for _ in range(2):
            b = subprocess.run(
                p.build, shell=True, cwd=fresh, env=env, capture_output=True, text=True, check=False
            )
            if b.returncode != 0:
                return f"`build` fails in a fresh copy of the project:\n{(b.stdout + b.stderr)[-1500:]}"
            sizes.append({f: f.stat().st_size for f in fresh.rglob("*") if f.is_file()})
        changed = [f for f in sizes[1] if f in sizes[0] and sizes[1][f] != sizes[0][f]]
        if changed:
            f = changed[0]
            return (
                f"running `build` twice gives a different tree: {f.relative_to(fresh)} is "
                f"{sizes[0][f]} bytes after one build and {sizes[1][f]} after two (does it append "
                "to a file without truncating it first?). Each build must produce the same files."
            )
        if p.test:
            t = subprocess.run(p.test, shell=True, cwd=fresh, capture_output=True, text=True, check=False)
            if t.returncode != 0:
                return (
                    f"`test` exited with {t.returncode} when run on its own after a build "
                    f"(it must not depend on files `run` creates):\n{(t.stdout + t.stderr)[-1500:]}"
                )
    if p.bench:
        b = subprocess.run(p.bench, shell=True, cwd=root, capture_output=True, text=True, check=False)
        lines = [ln for ln in b.stdout.splitlines() if ln.strip()]
        if b.returncode != 0 or not lines or not _NUMBER.findall(lines[-1]):
            return f"`bench` must print a number on its last line; it printed:\n{b.stdout[-800:]}"
    return None


async def init(root: Path, model, attempts: int = 4, hint: str = "", log=print) -> float:
    """Write a working oqed.toml in `root`; returns the model spend in USD."""
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": repo_context(root) + (f"\n\nNote from the user: {hint}" if hint else "")},
    ]
    spent = 0.0
    for attempt in range(1, attempts + 1):
        log(f"[init] asking {model.model} for oqed.toml (attempt {attempt}/{attempts})")
        reply = await model.complete(messages, effort="low", max_tokens=4000)
        spent += reply.cost_usd or 0.0
        toml, files = parse_reply(reply.text), parse_files(reply.text)
        write_files(root, files)
        for rel, text in files.items():
            log(f"  {rel}: {len(text.splitlines())} lines")
        log("\n".join("  | " + ln for ln in toml.splitlines()))
        problem = await asyncio.to_thread(check, root, toml, log)
        if problem is None:
            log(f"[init] wrote {root / proj.CONFIG}")
            return spent
        lines = [ln for ln in problem.splitlines() if ln.strip()]
        log(
            f"[init] doesn't work yet: {lines[0][:200]}"
            + (f" ... {lines[-1][:200]}" if len(lines) > 1 else "")
        )
        given = "".join(f'<file path="{rel}">\n{text}</file>\n' for rel, text in files.items())
        messages += [
            {"role": "assistant", "content": f"{given}<toml>\n{toml}</toml>"},
            {
                "role": "user",
                "content": f"That didn't work:\n{problem[-3000:]}\n\n"
                "Fix it. Reply with every file and the whole config.",
            },
        ]
    (root / proj.CONFIG).unlink(missing_ok=True)
    shutil.rmtree(root / SUPPORT, ignore_errors=True)
    raise RuntimeError(
        f"no working oqed.toml after {attempts} attempts; write one by hand (see `oqed init --help`)"
    )
