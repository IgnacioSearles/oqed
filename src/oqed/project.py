"""Whole-project mode: find every function oqed can work on and rank it by use.

    oqed scan        # in a directory with oqed.toml

`oqed.toml` names a `build` command (run with `CC` set to oqed-cc) and a `run`
command (the workload). Optionally `test`, whose output must not change (by
default `run`; when its output varies from run to run, only its exit status
counts), and `bench`, which prints one number, lower is better unless
`bench_higher_is_better` (by default oqed times `run`'s CPU time). Scanning:

1. Builds the project with every compile logged and `-g` added, then runs
   the workload, sampling where its time goes (`profile_rt.c`, see `read_time`).
2. Preprocesses each logged source with its own flags and walks clang's JSON
   AST for function definitions located in the project (the `.i` line markers
   say where each one came from).
3. Keeps leaf functions with 1-4 integer or pointer arguments and an
   integer, pointer or void result. Each becomes a target in
   `.oqed/targets/<name>/` whose `func.c` is a slice of the preprocessed
   source: the function plus the project typedefs, structs, enums, constants
   and helpers it uses. A target is dropped when the slice doesn't compile or
   the compiled function calls something. Functions on integers that read no
   global data run in the harness and can be proven (`oracle = "harness"`);
   the rest are judged by the project itself (`oracle = "project"`, see
   `oracle.py`).
4. Ranks targets by their share of the workload's time, most first.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .cc import _INT_TYPES
from .targets import MAX_ARGS as HARNESS_ARGS
from .targets import IntType, Target, load_target
from .toolchain import ToolError, compile_to_asm, instruction_lines

CONFIG = "oqed.toml"
STATE = ".oqed"
_PRELUDE = "".join(
    f"#include <{h}.h>\n" for h in ("stdint", "stddef", "stdbool", "limits", "string", "stdlib", "assert")
)
_STORAGE = re.compile(r"\b(static|inline|__inline__|__inline|extern)\b\s*")
_LOOPS = {"ForStmt", "WhileStmt", "DoStmt", "GotoStmt", "IndirectGotoStmt"}
_GPRS = {f"r{n}" for n in ("ax", "bx", "cx", "dx", "si", "di", "bp", "sp")} | {f"r{i}" for i in range(8, 16)}
_C_KEYWORDS = {"struct", "union", "enum", "const", "volatile", "restrict", "unsigned", "signed"}


@dataclass
class Project:
    root: Path
    build: str
    run: str
    cc: str = "clang"
    test: str | None = None  # deterministic output; the oracle for functions that touch memory
    bench: str | None = None  # prints one number; None: oqed times `run`
    bench_runs: int = 7
    bench_unit: str = "units"
    bench_higher_is_better: bool = False  # e.g. MB/s

    @property
    def test_command(self) -> str:
        return self.test or self.run

    @property
    def unit(self) -> str:
        if self.bench is None:
            return "CPU ms"
        return f"1/({self.bench_unit})" if self.bench_higher_is_better else self.bench_unit

    @property
    def state(self) -> Path:
        return self.root / STATE


def load(root: Path) -> Project:
    path = root / CONFIG
    if not path.is_file():
        raise FileNotFoundError(f"no {CONFIG} in {root} (needs `build` and `run` commands)")
    cfg = tomllib.loads(path.read_text())
    return Project(
        root=root.resolve(),
        build=cfg["build"],
        run=cfg["run"],
        cc=cfg.get("cc", "clang"),
        test=cfg.get("test"),
        bench=cfg.get("bench"),
        bench_runs=cfg.get("bench_runs", 7),
        bench_unit=cfg.get("bench_unit", "units"),
        bench_higher_is_better=cfg.get("bench_higher_is_better", False),
    )


@dataclass
class Function:
    """A function definition found in the project."""

    name: str  # target name (the function name, qualified by file stem on a clash)
    function: str
    location: str  # path relative to the project root, with the line
    time: float = 0.0  # share of the workload's time spent in its code (see `read_time`)
    target: Path | None = None  # set when oqed can search it
    oracle: str = "harness"  # "project" when judged by the project's test command
    skipped: str | None = None  # why not, otherwise


@dataclass
class Scan:
    functions: list[Function] = field(default_factory=list)  # every definition, most-called first

    @property
    def targets(self) -> list[Function]:
        return [f for f in self.functions if f.target is not None]


# --- record build and profile ---------------------------------------------------------


def _oqed_cc() -> str:
    """oqed-cc as one executable path, so any build system takes it as `CC`
    (CMake and configure scripts don't accept a command with arguments). The
    compiler it wraps comes from `$OQED_CC` (see `cc_env`)."""
    exe = Path(sys.executable).parent / "oqed-cc"
    if exe.is_file():
        return str(exe)
    if found := shutil.which("oqed-cc"):
        return found
    wrapper = Path.home() / ".cache" / "oqed" / "bin" / "oqed-cc"
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m oqed.cc "$@"\n')
    wrapper.chmod(0o755)
    return str(wrapper)


def cc_env(project: Project) -> dict[str, str]:
    """Environment for a build that compiles through oqed-cc."""
    return {"CC": _oqed_cc(), "OQED_CC": project.cc}


def _shell(command: str, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(command, shell=True, cwd=cwd, env=env, capture_output=True, text=True, check=False)


def record(project: Project) -> Path:
    """Build and run the workload in record mode; returns the record directory."""
    rec = project.state / "record"
    shutil.rmtree(rec, ignore_errors=True)
    rec.mkdir(parents=True)
    env = {**os.environ, **cc_env(project), "OQED_RECORD": str(rec)}
    b = _shell(project.build, project.root, env)
    if b.returncode != 0:
        raise RuntimeError(f"build failed ({project.build}):\n{(b.stdout + b.stderr)[-2000:]}")
    if not (rec / "commands.jsonl").exists():
        raise RuntimeError(f"`{project.build}` compiled nothing through $CC (does it rebuild, e.g. make -B?)")
    env = {**os.environ, "OQED_PROFILE": str(rec / "profile.txt")}
    r = _shell(project.run, project.root, env)
    if r.returncode != 0:
        raise RuntimeError(f"workload failed ({project.run}):\n{(r.stdout + r.stderr)[-2000:]}")
    return rec


def read_time(path: Path) -> dict[str, float]:
    """Share of the workload's time per function, over every process that
    wrote a section of the profile. A function's share is the larger of:

    - the samples in its symbol's address range: its own code plus whatever
      was inlined into it (what replacing the whole function can speed up);
    - the samples whose innermost inlined frame (from the debug info) is it:
      its own code wherever it was inlined (what a faster inline block can
      speed up).

    Shares are of all samples, including time in libraries and blocked."""
    sections: list[tuple[str, int, list[tuple[int, int]]]] = []
    if not path.exists():
        return {}
    for line in path.read_text().splitlines():
        key, _, value = line.partition(" ")
        if key == "exe":
            sections.append((value, 0, []))
        elif key == "anchor" and sections:
            sections[-1] = (sections[-1][0], int(value, 16), sections[-1][2])
        elif key == "pc" and sections:
            pc, n = value.split()
            sections[-1][2].append((int(pc, 16), int(n)))
    total = sum(n for _, _, pcs in sections for _, n in pcs)
    by_symbol: dict[str, int] = {}
    by_inline: dict[str, int] = {}
    for exe, anchor, pcs in sections:
        symbols = _Symbols(_symbols(Path(exe)))
        at = next((a for a, name in symbols.starts.items() if name == "__oqed_prof_anchor"), None)
        if at is None:
            continue
        bias = anchor - at
        local = [(pc - bias, n) for pc, n in pcs if symbols.containing(pc - bias)]
        for (pc, n), inner in zip(local, _innermost(Path(exe), [pc for pc, _ in local]), strict=True):
            name = symbols.containing(pc)
            by_symbol[name] = by_symbol.get(name, 0) + n  # type: ignore[index]
            if inner:
                by_inline[inner] = by_inline.get(inner, 0) + n
    names = set(by_symbol) | set(by_inline)
    return {f: max(by_symbol.get(f, 0), by_inline.get(f, 0)) / total for f in names} if total else {}


def _innermost(exe: Path, addrs: list[int]) -> list[str | None]:
    """The innermost inlined function at each address, from the debug info."""
    if not addrs:
        return []
    out = subprocess.run(
        ["llvm-symbolizer", f"--obj={exe}", "--inlining", "--output-style=JSON"],
        input="\n".join(hex(a) for a in addrs),
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    names: list[str | None] = []
    for line in out:
        frames = json.loads(line).get("Symbol", []) if line.strip() else []
        name = frames[0].get("FunctionName") if frames else None
        names.append(name if name and name != "??" else None)
    return names if len(names) == len(addrs) else [None] * len(addrs)


class _Symbols:
    """Function symbols of an executable, to look up an address."""

    def __init__(self, syms: dict[int, tuple[str, int]]):
        self.starts = {a: name for a, (name, _) in syms.items()}
        self.sizes = {a: size for a, (_, size) in syms.items()}
        self._sorted = sorted(syms)

    def containing(self, addr: int) -> str | None:
        i = bisect.bisect_right(self._sorted, addr) - 1
        if i < 0:
            return None
        start = self._sorted[i]
        return self.starts[start] if addr < start + self.sizes[start] else None


def _symbols(exe: Path) -> dict[int, tuple[str, int]]:
    nm = subprocess.run(["nm", "-S", "--defined-only", str(exe)], capture_output=True, text=True, check=False)
    syms: dict[int, tuple[str, int]] = {}
    for line in nm.stdout.splitlines():
        parts = line.split()
        # Sized code symbols only: a sizeless one (data_start, _end) would
        # swallow every address after it, libraries included.
        if len(parts) == 4 and parts[2] in "tTwW":
            syms[int(parts[0], 16)] = (parts[3], int(parts[1], 16))
    return syms


# --- discovery ------------------------------------------------------------------------


class _Unit:
    """One preprocessed translation unit and its AST."""

    def __init__(self, ipath: Path, cwd: Path):
        self.text = ipath.read_bytes()
        self.cwd = cwd
        self.markers: list[tuple[int, int, str]] = []  # (offset after the marker line, line, file)
        for m in re.finditer(rb'^# (\d+) "([^"]*)"[^\n]*\n', self.text, re.M):
            self.markers.append((m.end(), int(m.group(1)), m.group(2).decode()))
        self._starts = [m[0] for m in self.markers]
        out = subprocess.run(
            ["clang", "-Xclang", "-ast-dump=json", "-fsyntax-only", "-w", "-x", "cpp-output", str(ipath)],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        self.decls = [d for d in json.loads(out).get("inner", []) if _offset(d) is not None]
        self.by_id = {d["id"]: d for d in self.decls}
        self.defs = {d["name"]: d for d in self.decls if d.get("kind") == "FunctionDecl" and _body(d)}
        # A name can be a typedef and a struct tag at once (`typedef struct X X;`),
        # and a struct can be declared before it is defined: keep them all.
        self.types: dict[str, list[dict]] = {}
        for d in self.decls:
            if d.get("kind") in ("TypedefDecl", "RecordDecl", "EnumDecl") and d.get("name"):
                self.types.setdefault(d["name"], []).append(d)
        self.enum_of = {
            c["id"]: d["id"]
            for d in self.decls
            if d.get("kind") == "EnumDecl"
            for c in d.get("inner", [])
            if c.get("kind") == "EnumConstantDecl"
        }

    def origin(self, offset: int) -> tuple[Path, int]:
        i = bisect.bisect_right(self._starts, offset) - 1
        if i < 0:
            return Path("<unknown>"), 0
        start, line, name = self.markers[i]
        return (self.cwd / name).resolve(), line + self.text.count(b"\n", start, offset)

    def span(self, d: dict) -> tuple[int, int]:
        end = d["range"]["end"]
        return _offset(d), end["offset"] + end.get("tokLen", 1)  # type: ignore[return-value]


def _offset(d: dict) -> int | None:
    return d.get("range", {}).get("begin", {}).get("offset")


def _body(d: dict) -> dict | None:
    return next((x for x in d.get("inner", []) if x.get("kind") == "CompoundStmt"), None)


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            if isinstance(v, (dict, list)):
                yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _int(spelling: str) -> IntType | None:
    return _INT_TYPES.get(re.sub(r"\b(const|volatile)\b", "", spelling).strip())


def _resolve(unit: _Unit, spelling: str) -> IntType | None:
    """Map a type spelling to an IntType, following typedefs declared in the unit."""
    for _ in range(8):
        if (t := _int(spelling)) is not None:
            return t
        name = re.sub(r"\b(const|volatile)\b", "", spelling).strip()
        # Enums are passed as int (the compilers pick int unless a value doesn't fit).
        if name.startswith("enum ") or any(x.get("kind") == "EnumDecl" for x in unit.types.get(name, [])):
            return "i32"
        td = next((x for x in unit.types.get(name, []) if x.get("kind") == "TypedefDecl"), None)
        if td is None:
            return None
        # An anonymous enum's typedef "desugars" to its own name; its qualType says enum.
        desugared = td["type"].get("desugaredQualType", td["type"]["qualType"])
        spelling = td["type"]["qualType"] if desugared == name else desugared
    return None


# Arguments a function may take: past 6 they are on the stack, which a whole
# function reads like the C does.
MAX_ARGS = 12


@dataclass
class Signature:
    args: list[IntType]  # pointers are u64
    ret: IntType  # u64 for pointers and void
    ret_c: str | None = None  # the C return type when `ret` stands in for void or a pointer
    pointers: bool = False


def _value(unit: _Unit, spelling: str) -> tuple[IntType | None, bool]:
    """An argument or return type as passed in a register, and whether it is a pointer."""
    if re.sub(r"\b(const|volatile|restrict|__restrict)\b", "", spelling).rstrip().endswith("*"):
        return "u64", True
    return _resolve(unit, spelling), False


def signature(unit: _Unit, d: dict) -> Signature | str:
    """How the function passes its arguments and result, or why it is out of scope:
    integers or pointers (up to MAX_ARGS; more than 4 only as a whole function under
    the project oracle), an integer, pointer or no result."""
    t = d["type"]
    if "..." in t["qualType"]:
        return "variadic"
    parms = [x for x in d.get("inner", []) if x.get("kind") == "ParmVarDecl"]
    if not 1 <= len(parms) <= MAX_ARGS:
        return f"{len(parms)} arguments"
    vals = [_value(unit, p["type"].get("desugaredQualType", p["type"]["qualType"])) for p in parms]
    ret_spelling = t["qualType"].split("(")[0].strip()
    if ret_spelling == "void":
        ret, ret_ptr, ret_c = "u64", False, "void"
    else:
        ret, ret_ptr = _value(unit, ret_spelling)
        ret_c = ret_spelling if ret_ptr else None
    if ret is None or any(a is None for a, _ in vals):
        return f"arguments or result not integers or pointers: {t['qualType']}"
    return Signature([a for a, _ in vals], ret, ret_c, ret_ptr or any(p for _, p in vals))  # type: ignore[arg-type]


def slice_source(unit: _Unit, d: dict, root: Path) -> tuple[str, bool]:
    """func.c for definition `d`: the project declarations it needs, then the
    function itself made external. Also whether it (or a helper) has a loop."""
    need: dict[int, dict] = {}
    loops = False
    todo = [d]
    while todo:
        cur = todo.pop()
        if cur["id"] in need:
            continue
        need[cur["id"]] = cur
        if any(n.get("kind") in _LOOPS for n in _walk(cur)):
            loops = True
        for n in _walk(cur):
            ref = n.get("referencedDecl")
            if isinstance(ref, dict):
                kind, name = ref.get("kind"), ref.get("name")
                if kind == "FunctionDecl" and name in unit.defs:
                    todo.append(unit.defs[name])
                elif kind == "VarDecl" and ref["id"] in unit.by_id:
                    todo.append(unit.by_id[ref["id"]])
                elif kind == "EnumConstantDecl" and ref["id"] in unit.enum_of:
                    todo.append(unit.by_id[unit.enum_of[ref["id"]]])
            t = n.get("type")
            if isinstance(t, dict):
                for ident in re.findall(r"[A-Za-z_]\w*", t.get("qualType", "")):
                    if ident not in _C_KEYWORDS:
                        todo.extend(unit.types.get(ident, []))
    # Declarations from system headers come from the prelude's includes instead.
    spans = []
    for x in need.values():
        if x is d or root in unit.origin(_offset(x))[0].parents:  # type: ignore[arg-type]
            spans.append((*unit.span(x), x))
    # Prototypes of the helpers, so the order functions are defined in does not matter.
    # The function's own prototype too: helpers may call it back (recursion).
    protos = [
        _STORAGE.sub("", unit.text[unit.span(x)[0] : _offset(_body(x))].decode()).strip() + ";"  # type: ignore[arg-type]
        if x is d
        else unit.text[unit.span(x)[0] : _offset(_body(x))].decode().strip() + ";"  # type: ignore[arg-type]
        for _, _, x in sorted(spans, key=lambda s: s[0])
        if x.get("kind") == "FunctionDecl"
    ]
    # Types first (they never depend on functions), then the prototypes, then
    # globals and functions in source order: a prototype may name any type, and
    # a global's initializer may take a function's address.
    types, rest, last_end = [], [], -1
    for a, b, x in sorted(spans, key=lambda s: s[0]):
        if b <= last_end:
            continue  # inside a declaration already copied (e.g. the struct in a typedef)
        text = unit.text[a:b].decode()
        if x is d:
            body = _offset(_body(d))  # type: ignore[arg-type]
            head = _STORAGE.sub("", unit.text[a:body].decode())
            text = head + unit.text[body:b].decode()
        elif x.get("kind") != "FunctionDecl":
            text += ";"
        (types if x.get("kind") in ("TypedefDecl", "RecordDecl", "EnumDecl") else rest).append(text)
        last_end = b
    out = types + (["\n".join(protos)] if protos else []) + rest
    return _PRELUDE + "\n" + "\n\n".join(out) + "\n", loops


# Functions both compilers reduce to this many instructions (plus `ret`) aren't worth a search.
MINIMAL = 2
# Past this many instructions (from both compilers) a function is too big to
# rewrite whole, and its listings alone would fill the prompt.
MAXIMUM = 1500


def _check(target: Target) -> tuple[str | None, bool]:
    """Why oqed can't search this target, if it can't, and whether the
    compiled code needs the project to run: it reads global data or calls
    other functions (so the harness can't run it alone)."""
    reads_globals = False
    sizes = []
    for compiler in ("clang", "gcc"):
        try:
            asm = compile_to_asm(target, compiler)
        except ToolError as e:
            lines = str(e).strip().splitlines()
            first = next((ln for ln in lines if "error:" in ln), lines[-1])
            return f"{compiler}: {first.split('error:', 1)[-1].strip()[:150]}", False
        for line in asm.splitlines():
            ins = line.strip()
            # `call`, and `jmp name` (a tail call) leave the function;
            # `jmp .L5` and `jmp rcx` (a switch's jump table) don't.
            op = ins.split(None, 1)[-1] if ins.startswith(("jmp", "call")) else ""
            if re.fullmatch(r"[A-Za-z_][\w.@]*", op) and not op.startswith(".L") and op not in _GPRS:
                reads_globals = True
            reads_globals |= "rip" in ins or ins.startswith("call")
        sizes.append(len([ln for ln in instruction_lines(asm) if ln != "ret"]))
    if max(sizes) <= MINIMAL:
        return f"already minimal ({max(sizes)} instructions)", reads_globals
    if min(sizes) > MAXIMUM:
        return f"too big to rewrite ({min(sizes)} instructions)", reads_globals
    return None, reads_globals


def clang_unit(flags: list[str], src: str, cwd: Path, out: Path) -> _Unit | None:
    """The source preprocessed by clang with these flags, whatever the build's
    compiler (gcc's headers expand `assert` and friends differently)."""
    pre = subprocess.run(
        ["clang", *flags, "-E", src, "-o", str(out)], cwd=cwd, capture_output=True, check=False
    )
    return _Unit(out, cwd) if pre.returncode == 0 else None


def source_sha(unit: _Unit | None, function: str, root: Path) -> str | None:
    """The hash a project-oracle lock entry records: the function's slice as
    clang preprocesses it. oqed-cc substitutes only while it is unchanged."""
    d = unit.defs.get(function) if unit else None
    return None if d is None else slice_sha(slice_source(unit, d, root.resolve())[0], root)  # type: ignore[arg-type]


def slice_sha(src: str, root: Path) -> str:
    """Hash of a slice, the same however the build spells paths: line markers
    are dropped and the project root (or `./`) is cut from string literals
    (`assert` puts `__FILE__` in the code; `oqed scan` preprocesses with
    absolute paths, a build usually with relative ones)."""
    lines = [ln for ln in src.splitlines() if not re.match(r"#\s*\d+\s", ln)]
    text = " ".join(" ".join(lines).split())
    text = re.sub(r'"(?:' + re.escape(str(root.resolve())) + r"/|\./)", '"', text)
    return hashlib.sha256(text.encode()).hexdigest()


def discover(project: Project, rec: Path) -> Scan:
    time = read_time(rec / "profile.txt")
    targets_dir = project.state / "targets"
    shutil.rmtree(targets_dir, ignore_errors=True)
    targets_dir.mkdir(parents=True)
    work = project.state / "work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)

    seen: dict[tuple[str, int], tuple[_Unit, dict, Path, int, _Unit | None]] = {}
    commands = [json.loads(ln) for ln in (rec / "commands.jsonl").read_text().splitlines() if ln.strip()]
    for k, cmd in enumerate(commands):
        for src in cmd["sources"]:
            ipath = work / f"{k}.{Path(src).stem}.i"
            pre = subprocess.run(
                [cmd["cc"], *cmd["flags"], "-E", src, "-o", str(ipath)],
                cwd=cmd["cwd"],
                capture_output=True,
                text=True,
                check=False,
            )
            if pre.returncode != 0:
                continue
            unit = _Unit(ipath, Path(cmd["cwd"]))
            hashed = unit if "clang" in Path(cmd["cc"]).name else None
            for d in unit.defs.values():
                file, line = unit.origin(_offset(d))  # type: ignore[arg-type]
                if project.root in file.parents and (str(file), line) not in seen:
                    if hashed is None:
                        hashed = clang_unit(
                            cmd["flags"], src, Path(cmd["cwd"]), work / f"{k}.{Path(src).stem}.clang.i"
                        )
                    seen[(str(file), line)] = (unit, d, file, line, hashed)

    names = [d["name"] for _, d, _, _, _ in seen.values()]
    scan = Scan()
    for unit, d, file, line, hashed in seen.values():
        fn = d["name"]
        name = fn if names.count(fn) == 1 else f"{fn}.{file.stem}"
        f = Function(name, fn, f"{file.relative_to(project.root)}:{line}", time.get(fn, 0.0))
        scan.functions.append(f)
        if fn == "main":
            f.skipped = "main"
            continue
        sig = signature(unit, d)
        if isinstance(sig, str):
            f.skipped = sig
            continue
        src, loops = slice_source(unit, d, project.root)
        tdir = targets_dir / name
        tdir.mkdir()
        (tdir / "func.c").write_text(src)
        meta = {
            "function": fn,
            "category": "project",
            "args": sig.args,
            "ret": sig.ret,
            "loop_free": not loops,
            "source": f.location,
            "license": "project",
            "project": str(project.root),
            "slice_sha": source_sha(hashed, fn, project.root),
        }
        if sig.ret_c:
            meta["ret_c"] = sig.ret_c
        if len(sig.args) > HARNESS_ARGS:
            meta["oracle"] = "project"  # the harness and inline blocks take 4 register arguments
        (tdir / "meta.toml").write_text(_toml(meta))
        why, reads_globals = _check(load_target(tdir))
        if why is None and (sig.pointers or sig.ret_c or reads_globals):
            meta["oracle"] = "project"
            (tdir / "meta.toml").write_text(_toml(meta))
        if why:
            f.skipped = why
            shutil.rmtree(tdir)
        else:
            f.target = tdir
            f.oracle = meta.get("oracle", "harness")
    scan.functions = rank(scan.functions)
    return scan


def rank(functions: list[Function]) -> list[Function]:
    """Search order: most time first; functions never seen running go last."""
    return sorted(functions, key=lambda f: (-f.time, f.name))


def _toml(meta: dict) -> str:
    return "".join(f"{k} = {json.dumps(v)}\n" for k, v in meta.items())


def scan(project: Project) -> Scan:
    return discover(project, record(project))
