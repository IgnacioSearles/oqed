"""oqed-cc: a compiler wrapper that applies `oqed.lock` at build time.

    make CC=oqed-cc                    # wraps clang (or $OQED_CC)
    make CC="oqed-cc --cc=gcc"

For each C source in the command line:

1. Preprocess it with the build's own flags, so functions defined in headers
   (stb_image.h, FFmpeg's common.h, ...) are found like any other.
2. Find each lock entry's function definition with clang's JSON AST and check
   its signature and that the entry only uses instructions the build's
   `-march` allows.
3. Replace the function body with the asm, padded to the same number of
   lines so line numbers and diagnostics after it don't move.
4. Prove the substitution: compile the original and the rewritten source
   with the build's compiler and flags, each with a wrapper that calls the
   function, and check the two wrappers' machine code is equivalent with the
   SMT verifier. This is against the function as it is in the source now, so
   a stale lock entry is caught rather than trusted.
5. Vectorization guard: the same compiles report which loops the compiler
   vectorized. If a substitution makes a loop lose vectorization, that
   substitution is dropped.
6. Compile the rewritten preprocessed source with the original command.

Lock entries with status `tested` (written by `oqed optimize --unsafe`) are
substituted when the proof is inconclusive but differential testing of the
compiled code passes, with an `UNSAFE` note. A counterexample always keeps
the C.

Anything unexpected falls back to compiling the original command unchanged;
oqed-cc never breaks a build. Decisions are reported on stderr.

Record mode (`OQED_RECORD=<dir>`, used by `oqed scan`) substitutes nothing: it
logs each compile command to `<dir>/commands.jsonl`, builds with
`-finstrument-functions`, and links in a call counter (`profile_rt.c`).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import inline, lock
from .isa import MARCH_FEATURES, isa_violations
from .targets import IntType, Target
from .toolchain import ToolError, assemble, extract_function, relocations

# Spellings clang's AST uses for (desugared) integer types.
_INT_TYPES: dict[str, IntType] = {
    "char": "i8",
    "signed char": "i8",
    "unsigned char": "u8",
    "short": "i16",
    "unsigned short": "u16",
    "int": "i32",
    "unsigned int": "u32",
    "long": "i64",
    "unsigned long": "u64",
    "long long": "i64",
    "unsigned long long": "u64",
}

# Flags that take a value in the next argument.
_WITH_VALUE = {"-o", "-MF", "-MT", "-MQ", "-include", "-imacros", "-I", "-isystem", "-iquote", "-D", "-U"}
# Not needed (or harmful) when preprocessing or emitting assembly.
_OUTPUT_FLAGS = {"-c", "-S", "-MD", "-MMD", "-MP", "-shared", "-static", "-pie", "-no-pie", "-rdynamic"}
_PASSTHROUGH = {"-E", "-M", "-MM", "-x", "-"}

_REMARK = re.compile(r"^(.*?):(\d+):\d+: (?:remark: vectorized loop|optimized: loop vectorized)", re.M)


def note(msg: str) -> None:
    if not os.environ.get("OQED_QUIET"):
        print(f"oqed-cc: {msg}", file=sys.stderr)


@dataclass
class Command:
    sources: list[int]  # indices of C sources in argv
    flags: list[str]  # for preprocessing and -S compiles (no inputs, outputs, link-only flags)
    march: str


def parse(argv: list[str]) -> Command | None:
    """None when the command is not a plain C compile oqed-cc should touch."""
    sources, flags = [], []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in _PASSTHROUGH or a.startswith("-flto"):
            return None
        if a in _WITH_VALUE:
            if a not in ("-o", "-MF", "-MT", "-MQ"):
                flags += [a, argv[i + 1]]
            i += 2
            continue
        if a.endswith(".c") and not a.startswith("-"):
            sources.append(i)
        elif a in _OUTPUT_FLAGS or a.startswith(("-l", "-L", "-Wl,")):
            pass
        elif a.startswith("-") or not a.endswith((".o", ".a", ".so")):
            flags.append(a)
        i += 1
    if not sources:
        return None
    march = next((a.split("=", 1)[1] for a in reversed(flags) if a.startswith("-march=")), "x86-64")
    return Command(sources, flags, march)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


@dataclass
class Definition:
    name: str
    body: tuple[int, int]  # byte offsets of `{ ... }` in the preprocessed file
    start: int  # byte offset where the definition begins
    params: list[str]
    param_types: list[str]  # as spelled in the source
    args: list[IntType]
    ret_spelling: str
    ret: IntType


def _ast(path: Path, name: str) -> list[dict]:
    out = _run(
        ["clang", "-Xclang", "-ast-dump=json", "-Xclang", f"-ast-dump-filter={name}"]
        + ["-fsyntax-only", "-w", "-x", "cpp-output", str(path)]
    ).stdout
    objs, pos, dec = [], 0, json.JSONDecoder()
    while (pos := out.find("{", pos)) != -1:
        obj, pos = dec.raw_decode(out, pos)
        objs.append(obj)
    return objs


def _int_type(spelling: str, path: Path, depth: int = 0) -> IntType | None:
    """Map a type spelling to an IntType, resolving typedefs through the AST.
    Pointers are u64, as passed in registers."""
    s = re.sub(r"\b(const|volatile)\b", "", spelling).strip()
    if s.endswith("*"):
        return "u64"
    if s in _INT_TYPES:
        return _INT_TYPES[s]
    if depth > 4 or not re.fullmatch(r"\w+", s):
        return None
    for d in _ast(path, s):
        if d.get("kind") == "TypedefDecl" and d.get("name") == s:
            t = d["type"]
            return _int_type(t.get("desugaredQualType", t["qualType"]), path, depth + 1)
    return None


def find_definition(path: Path, name: str) -> Definition | str:
    """The function's definition in a preprocessed file, or why it can't be used."""
    for d in _ast(path, name):
        if d.get("kind") != "FunctionDecl" or d.get("name") != name:
            continue
        body = next((x for x in d.get("inner", []) if x.get("kind") == "CompoundStmt"), None)
        if body is None:
            continue  # a prototype
        parms = [x for x in d.get("inner", []) if x.get("kind") == "ParmVarDecl"]
        if any("name" not in p for p in parms):
            return "has unnamed parameters"
        spellings = [p["type"]["qualType"] for p in parms]
        args = [_int_type(p["type"].get("desugaredQualType", p["type"]["qualType"]), path) for p in parms]
        ret_spelling = d["type"]["qualType"].split("(")[0].strip()
        ret = "u64" if ret_spelling == "void" else _int_type(ret_spelling, path)
        if ret is None or any(a is None for a in args):
            return f"signature {d['type']['qualType']!r} is not integers in registers"
        end = body["range"]["end"]
        return Definition(
            name,
            (body["range"]["begin"]["offset"], end["offset"] + end.get("tokLen", 1)),
            d["range"]["begin"]["offset"],
            [p["name"] for p in parms],
            spellings,
            args,  # type: ignore[arg-type]
            ret_spelling,
            ret,
        )
    return "no definition in this file"


@dataclass
class Build:
    """What a substitution needs to know about the compile it goes into."""

    path: Path  # the preprocessed source
    gcc: bool = False
    intel: bool = False  # -masm=intel


def _declared(path: Path, name: str) -> bool:
    return any(
        d.get("kind") in ("FunctionDecl", "VarDecl") and d.get("name") == name for d in _ast(path, name)
    )


@dataclass
class Substitution:
    """A lock entry applied to a definition. Usually the body becomes inline
    asm, so callers keep inlining the function. Entries judged by the
    project's tests may instead need to be a function of their own (they use
    the stack, call other functions or tail-call): the definition is marked
    `naked` and its body is the candidate, a whole System V function."""

    entry: lock.LockEntry
    definition: Definition
    build: Build | None = None
    body: str = ""  # replacement C body
    attrs: str = ""  # inserted before the definition
    function: bool = False

    def __post_init__(self) -> None:
        d, asm = self.definition, self.entry.asm
        memory = self.entry.oracle == "project"
        if not (memory and inline.needs_function(asm)):
            self.body = inline.asm_body(asm, d.args, d.params, ret_cast=d.ret_spelling, memory=memory)
            return
        b = self.build or Build(Path("."))
        self.function = True
        # noipa: gcc must not assume what the asm clobbers or that it is pure
        # (clang has no such analyses by default, and no such attribute).
        self.attrs = "__attribute__((naked, noipa)) " if b.gcc else "__attribute__((naked)) "
        # Taking the addresses keeps static callees the asm calls (the
        # compiler can't see those calls and might drop or inline them all),
        # and keeps the function's own calling convention standard.
        syms = sorted({sym for _, sym in relocations(asm) if sym and sym != d.name})
        keep = [d.name] + [sym for sym in syms if _declared(b.path, sym)]
        refs = ", ".join(f"(void *)&{sym}" for sym in keep)
        self.body = (
            inline.naked_body(asm, d.name, b.intel)
            + f" static void *const __oqed_keep_{d.name}[] __attribute__((used)) = {{{refs}}};"
        )


def rewrite(text: bytes, subs: list[Substitution]) -> bytes:
    for s in sorted(subs, key=lambda s: s.definition.start, reverse=True):
        a, b = s.definition.body
        # Keep the line count so line markers, diagnostics and the
        # vectorization guard's loop locations after the function don't move.
        text = text[:a] + s.body.encode() + b"\n" * text[a:b].count(b"\n") + text[b:]
        start = s.definition.start
        text = text[:start] + s.attrs.encode() + text[start:]
    return text


_MARKER = re.compile(rb'^# (\d+) "([^"]*)"', re.M)


def _source_lines(text: bytes, d: Definition) -> set[tuple[str, int]]:
    """(file, line) of every source line the definition spans, as compiler
    remarks name them (through the preprocessed file's line markers)."""
    a, b = d.start, d.body[1]
    marks = [m for m in _MARKER.finditer(text, 0, b)]
    out: set[tuple[str, int]] = set()
    pos, file, line = 0, "", 1
    for m in marks + [None]:
        end = m.start() if m else b
        if end > a:
            first = line + text[pos : max(a, pos)].count(b"\n")
            last = line + text[pos:end].count(b"\n")
            out |= {(file, n) for n in range(first, last + 1)}
        if m is None:
            break
        pos, line, file = text.index(b"\n", m.start()) + 1, int(m.group(1)), m.group(2).decode()
    return out


def _wrapper(d: Definition) -> str:
    params = ", ".join(f"{t} __oqed_p{i}" for i, t in enumerate(d.param_types))
    call = ", ".join(f"__oqed_p{i}" for i in range(len(d.params)))
    return (
        f"\n__attribute__((noinline, used)) {d.ret_spelling} __oqed_check_{d.name}({params})"
        f" {{ return {d.name}({call}); }}\n"
    )


@dataclass
class Compiled:
    asm: str
    vectorized: set[tuple[str, int]] = field(default_factory=set)


def _compile_s(cc: str, flags: list[str], remark_flags: list[str], path: Path) -> Compiled:
    out = path.with_suffix(".s")
    proc = _run(
        [cc, *flags, "-S", "-masm=intel", "-fno-asynchronous-unwind-tables", "-Wno-error"]
        + [*remark_flags, "-o", str(out), str(path)]
    )
    if proc.returncode != 0:
        raise ToolError(f"{cc} -S failed:\n{proc.stderr.strip()[-2000:]}")
    vec = {(f, int(n)) for f, n in _REMARK.findall(proc.stderr + proc.stdout)}
    return Compiled(out.read_text(), vec)


def _is_clang(cc: str) -> bool:
    return "clang" in _run([cc, "--version"]).stdout


def _build(cc: str, cmd: Command, ipath: Path) -> Build:
    return Build(ipath, gcc=not _is_clang(cc), intel="-masm=intel" in cmd.flags)


def _prove(sub: Substitution, original: Compiled, rewritten: Compiled) -> tuple[str, str]:
    """("equivalent" | "unsafe" | "kept", why). `unsafe` means a `tested` entry
    whose proof was inconclusive passed differential testing instead."""
    import logging

    logging.getLogger("angr").setLevel(logging.CRITICAL)  # e.g. "unicornlib.so" missing; not our concern
    from . import harness, verify

    d = sub.definition
    name = f"__oqed_check_{d.name}"
    try:
        ref = assemble(extract_function(original.asm, name))
        new = assemble(extract_function(rewritten.asm, name))
    except ToolError as e:
        return "kept", f"could not extract the compiled check wrapper ({e})"
    t = Target(
        name=d.name,
        function=name,
        category="build",
        args=d.args,
        ret=d.ret,
        loop_free=True,
        source="",
        license="",
        c_source="",
        path=Path("."),
    )
    r = verify.check_sync(t, ref, new)
    if r.status == "equivalent":
        return "equivalent", ""
    why = f"proof {r.status}: {r.reason or 'counterexample found'}"
    if r.status == "unknown" and sub.entry.status == "tested":
        try:
            tested = harness.difftest(t, ref, new)
        except RuntimeError as e:
            return "kept", f"{why}; could not test ({e})"
        if tested.passed:
            return "unsafe", f"{why}; {tested.inputs_tested} test inputs agree"
        return "kept", f"{why}; testing failed: {tested.reason}"
    return "kept", why


def _slice_sha(cc: str, cmd: Command, src: str, ipath: Path, function: str, root: Path) -> str | None:
    """The same hash `oqed scan` records for a function: its definition and
    the project declarations it uses, as clang preprocesses the file."""
    from . import project

    unit = (
        project._Unit(ipath, Path.cwd())
        if _is_clang(cc)
        else project.clang_unit(cmd.flags, src, Path.cwd(), ipath.with_suffix(".clang.i"))
    )
    return project.source_sha(unit, function, root)


def _cache_dir() -> Path:
    return Path(os.environ.get("OQED_CACHE", Path.home() / ".cache" / "oqed")) / "cc"


def process_source(cc: str, cmd: Command, src: str, entries: list[lock.LockEntry], work: Path) -> Path | None:
    """The rewritten preprocessed file to compile instead of `src`, or None
    to compile `src` unchanged.

    Decisions are cached by the preprocessed source, the lock entries, the
    compiler's version and the flags: an unchanged rebuild skips the proofs."""
    ipath = work / (Path(src).stem + ".i")
    pre = _run([cc, *cmd.flags, "-E", src, "-o", str(ipath)])
    if pre.returncode != 0:
        return None  # let the real compile report the error
    text = ipath.read_bytes()
    wanted = [e for e in entries if e.function.encode() in text]
    if not wanted:
        return None

    key = hashlib.sha256()
    # oqed's own code too: a fixed oqed must not reuse an old decision.
    ours = b"".join(f.read_bytes() for f in sorted(Path(__file__).parent.glob("*.py")))
    for part in (text, ours, _run([cc, "--version"]).stdout.encode(), json.dumps(cmd.flags).encode()):
        key.update(part)
    for e in wanted:
        key.update(e.model_dump_json().encode())
    cached = _cache_dir() / f"{key.hexdigest()}.json"
    if os.environ.get("OQED_TRUST"):
        decision = _decide(cc, cmd, src, text, ipath, wanted, work)
    elif cached.is_file():
        decision = json.loads(cached.read_text())
    else:
        decision = _decide(cc, cmd, src, text, ipath, wanted, work)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(decision))
    for msg in decision["notes"]:
        note(msg)
    if applied := os.environ.get("OQED_APPLIED"):
        with open(applied, "a") as f:
            f.writelines(f"{fn}\n" for fn in decision["kept"])
    if not decision["kept"]:
        return None
    build = _build(cc, cmd, ipath)
    subs = [
        Substitution(e, find_definition(ipath, e.function), build)  # type: ignore[arg-type]
        for e in wanted
        if e.function in decision["kept"]
    ]
    out = work / "final" / (Path(src).stem + ".i")
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(rewrite(text, subs))
    return out


def _decide(
    cc: str, cmd: Command, src: str, text: bytes, ipath: Path, wanted: list[lock.LockEntry], work: Path
) -> dict:
    """Which lock entries to substitute in this source: {"kept": [function], "notes": [message]}."""
    notes: list[str] = []
    subs: list[Substitution] = []
    build = _build(cc, cmd, ipath)
    for e in wanted:
        if e.march not in MARCH_FEATURES or cmd.march not in MARCH_FEATURES:
            notes.append(f"{src}: {e.function}: kept C (unknown -march {cmd.march!r})")
            continue
        if bad := isa_violations(assemble(e.asm, relocations=e.oracle == "project"), cmd.march):
            notes.append(f"{src}: {e.function}: kept C (needs {', '.join(bad)} beyond -march={cmd.march})")
            continue
        d = find_definition(ipath, e.function)
        if isinstance(d, str):
            if d != "no definition in this file":
                notes.append(f"{src}: {e.function}: kept C ({d})")
            continue
        if (d.args, d.ret) != (e.args, e.ret):
            notes.append(f"{src}: {e.function}: kept C (signature changed since the lock entry)")
            continue
        try:
            subs.append(Substitution(e, d, build))
        except inline.NotInlinable as ex:
            notes.append(f"{src}: {e.function}: kept C (not inlinable: {ex})")
    if not subs:
        return {"kept": [], "notes": notes}
    if os.environ.get("OQED_TRUST"):
        # The project oracle's own builds: it tests the result itself.
        return {"kept": [s.entry.function for s in subs], "notes": notes}

    # Entries judged by the project's tests can't be proven; they apply only
    # to the source they were tested against.
    unsafe: dict[str, str] = {}
    lock_path = Path(os.environ["OQED_LOCK"]) if os.environ.get("OQED_LOCK") else lock.find(Path.cwd())
    root = lock_path.parent if lock_path else Path.cwd()  # the lock sits in the project root
    harness_subs = []
    for s in subs:
        if s.entry.oracle != "project":
            harness_subs.append(s)
        elif _slice_sha(cc, cmd, src, ipath, s.entry.function, root) == s.entry.slice_sha:
            unsafe[s.entry.function] = "tested with the project's test command, source unchanged since"
        else:
            notes.append(f"{src}: {s.entry.function}: kept C (source changed since it was tested)")
    subs = harness_subs + [s for s in subs if s.entry.function in unsafe]
    if not subs:
        return {"kept": [], "notes": notes}

    remark_flags = ["-Rpass=loop-vectorize"] if _is_clang(cc) else ["-fopt-info-vec-optimized"]
    wrappers = "".join(_wrapper(s.definition) for s in subs).encode()

    # These compiles emit Intel syntax (`_compile_s`), so whole-function
    # substitutions must hand the assembler back in Intel syntax.
    intel = {
        s.entry.function: dataclasses.replace(s, build=dataclasses.replace(build, intel=True))
        for s in subs
        if s.function
    }

    def compile_variant(tag: str, chosen: list[Substitution]) -> Compiled:
        p = work / f"{Path(src).stem}.{tag}.i"
        chosen = [intel.get(s.entry.function, s) for s in chosen]
        p.write_bytes(rewrite(text, chosen) + wrappers)
        return _compile_s(cc, cmd.flags, remark_flags, p)

    original = compile_variant("orig", [])
    rewritten = compile_variant("all", subs)
    proven = [s for s in subs if s.entry.function in unsafe]
    for s in subs:
        if s.entry.function in unsafe:
            continue
        verdict, why = _prove(s, original, rewritten)
        if verdict == "kept":
            notes.append(f"{src}: {s.entry.function}: kept C ({why})")
        else:
            proven.append(s)
            if verdict == "unsafe":
                unsafe[s.entry.function] = why
    if proven and len(proven) < len(subs):
        rewritten = compile_variant("proven", proven)
    subs = proven

    # Loops inside the substituted functions are replaced along with them;
    # only loops elsewhere (callers the code is inlined into) count.
    inside = set().union(*(_source_lines(text, s.definition) for s in subs))
    original.vectorized -= inside
    if subs and original.vectorized - rewritten.vectorized:
        keep = []
        for s in subs:
            alone = original.vectorized - compile_variant(f"only-{s.entry.function}", [s]).vectorized
            if alone:
                where = ", ".join(f"{f}:{n}" for f, n in sorted(alone))
                notes.append(f"{src}: {s.entry.function}: kept C (a loop would stop vectorizing at {where})")
            else:
                keep.append(s)
        subs = keep
        if subs and original.vectorized - compile_variant("kept", subs).vectorized:
            notes.append(f"{src}: kept C for all functions (substitutions together stop a loop vectorizing)")
            subs = []

    opts = " ".join(a for a in cmd.flags if a.startswith(("-O", "-march")))
    for s in subs:
        if why := unsafe.get(s.entry.function):
            notes.append(f"{src}: {s.entry.function}: UNSAFE: substituted on testing only ({why})")
            continue
        notes.append(
            f"{src}: {s.entry.function}: substituted (proven equivalent to this source with "
            f"{Path(cc).name} {opts}; {s.entry.speedup}x inlined latency when found)"
        )
    return {"kept": [s.entry.function for s in subs], "notes": notes}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cc = os.environ.get("OQED_CC", "clang")
    if argv and argv[0].startswith("--cc="):
        cc = argv.pop(0).split("=", 1)[1]
    with tempfile.TemporaryDirectory(prefix="oqed-cc-") as d:
        try:
            final = _plan(cc, argv, Path(d))
        except Exception as e:  # never break a build because of oqed
            note(f"internal error, compiling unchanged: {type(e).__name__}: {e}")
            final = [cc, *argv]
        return subprocess.call(final)


PROFILE_RT = Path(__file__).with_name("profile_rt.c")


def _record(cc: str, argv: list[str], rec: Path) -> list[str]:
    """Log the compile, instrument it, and link the call counter into executables."""
    rec.mkdir(parents=True, exist_ok=True)
    cmd = parse(argv)
    if cmd is not None:
        line = {
            "cwd": os.getcwd(),
            "cc": cc,
            "flags": cmd.flags,
            "sources": [str(Path(argv[i]).resolve()) for i in cmd.sources],
        }
        with open(rec / "commands.jsonl", "a") as f:
            f.write(json.dumps(line) + "\n")
    out = [cc, "-finstrument-functions", *argv]
    if not {"-c", "-S", "-E", "-M", "-MM"} & set(argv):
        obj = rec / f"profile_rt.{Path(cc).name}.o"
        if not obj.exists():
            subprocess.run([cc, "-O2", "-fPIC", "-c", str(PROFILE_RT), "-o", str(obj)], check=True)
        out.append(str(obj))
    return out


def _plan(cc: str, argv: list[str], work: Path) -> list[str]:
    if rec := os.environ.get("OQED_RECORD"):
        return _record(cc, argv, Path(rec))
    cmd = parse(argv)
    path = Path(os.environ["OQED_LOCK"]) if os.environ.get("OQED_LOCK") else lock.find(Path.cwd())
    if cmd is None or path is None:
        return [cc, *argv]
    entries = lock.load(path)
    out = list(argv)
    for i in cmd.sources:
        sub_dir = work / str(i)
        sub_dir.mkdir()
        if (rewritten := process_source(cc, cmd, argv[i], entries, sub_dir)) is not None:
            out[i] = str(rewritten)
    return [cc, *out]


if __name__ == "__main__":
    sys.exit(main())
