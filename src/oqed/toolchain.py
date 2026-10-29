"""Compiler, assembler and llvm-mca wrappers.

All assembly in oqed is a function body in Intel syntax (`.intel_syntax noprefix`):
instructions and local labels only, no directives, ending in `ret`.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from elftools.elf.elffile import ELFFile
from pydantic import BaseModel

from .targets import ARG_REGS, Target, bit_width

Compiler = Literal["clang", "gcc"]

DEFAULT_MARCH = "x86-64-v3"
DEFAULT_MCPU = "znver3"

# Keep the emitted code free of things that are noise for a superoptimizer
# (CET landing pads, unwind tables, stack protector).
CFLAGS = [
    "-O3",
    "-masm=intel",
    "-fno-asynchronous-unwind-tables",
    "-fno-unwind-tables",
    "-fcf-protection=none",
    "-fno-stack-protector",
]

_LABEL = re.compile(r"^(\.?L[\w.$]+):")


class ToolError(RuntimeError):
    pass


def _run(cmd: list[str], stdin: str | None = None) -> str:
    proc = subprocess.run(cmd, check=False, input=stdin, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ToolError(f"{cmd[0]} failed:\n{proc.stderr.strip()}")
    return proc.stdout


def cxx_driver(cc: str) -> str:
    """The C++ driver of a C compiler: clang -> clang++, gcc-13 -> g++-13."""
    path = Path(cc)
    name = path.name
    if "++" in name:
        return cc
    if "clang" in name:
        name = name.replace("clang", "clang++", 1)
    elif "gcc" in name:
        name = name.replace("gcc", "g++", 1)
    elif name == "cc":
        name = "c++"
    return str(path.with_name(name)) if path.parent != Path(".") else name


def c_driver(cxx: str) -> str:
    """The C driver of a C++ compiler: clang++ -> clang, g++ -> gcc."""
    path = Path(cxx)
    name = path.name.replace("clang++", "clang").replace("g++", "gcc").replace("c++", "cc")
    return str(path.with_name(name)) if path.parent != Path(".") else name


def lang_flags(target: Target) -> list[str]:
    return [f"-std={target.std}"] if target.std else []


def compiler_for(target: Target, compiler: str) -> str:
    return cxx_driver(compiler) if target.lang == "c++" else compiler


def compile_to_asm(target: Target, compiler: Compiler, march: str = DEFAULT_MARCH) -> str:
    """Compile the target and return just its function body."""
    cmd = [compiler_for(target, compiler), *CFLAGS, *lang_flags(target), f"-march={march}"]
    out = _run([*cmd, "-S", "-o", "-", str(target.c_file)])
    try:
        body = extract_function(out, target.sym)
    except ToolError:
        # gcc marks a function in an anonymous namespace internal in its
        # symbol (`_GLOBAL__N_1L3rotEj`); clang doesn't.
        alt = re.sub(r"(_GLOBAL__N_1)(\d+)", r"\1L\2", target.sym)
        if alt == target.sym:
            raise
        body = extract_function(out, alt)
    return gcc_addresses(body) if compiler == "gcc" else body


def record_layouts(target: Target, limit: int = 200) -> str:
    """clang's layout of each struct, union and class the slice defines: the
    offset of every field, so code that reads them needn't count bytes."""
    cmd = [compiler_for(target, "clang"), *CFLAGS, *lang_flags(target), f"-march={DEFAULT_MARCH}"]
    try:
        out = _run([*cmd, "-c", "-o", "/dev/null", "-Xclang", "-fdump-record-layouts", str(target.c_file)])
    except ToolError:
        return ""
    defined = set(re.findall(r"\b(?:struct|union|class)\s+(\w+)[^;{()]*\{", target.c_source))
    blocks: list[list[str]] = []
    keep = False
    for line in out.splitlines():
        if m := re.fullmatch(r"\s+0 \| (?:struct|union|class) ([\w:]+)", line):
            keep = m.group(1).split("::")[-1] in defined
            if keep:
                blocks.append([line.split("|", 1)[1].strip()])
        elif keep and (m := re.fullmatch(r"\s*([\d:-]+) \|   (\S.*)", line)):
            field = re.sub(r"::\(unnamed at [^)]*\)", "::(unnamed)", m.group(2))
            blocks[-1].append(f"{m.group(1):>6}  {field}")  # a top-level field, not a nested one
        elif keep and (m := re.search(r"sizeof=(\d+)", line)):
            blocks[-1][0] += f", {m.group(1)} bytes (field offsets in bytes):"
            keep = False
    lines = [ln for b in blocks for ln in b]
    return "\n".join(lines[:limit])


# gcc's Intel syntax puts the displacement outside the brackets.
_GCC_ADDRESS = re.compile(r"(?<![\w.$])(-?[\w.$@]+(?:[+-]\d+)?)\[([^\]]+)\]")


def gcc_addresses(body: str) -> str:
    """`QWORD PTR 8[rsp]` -> `QWORD PTR [rsp+8]`, `-4[rbp]` -> `[rbp-4]`, `x[rip]` ->
    `[rip+x]`: the form clang writes, the models expect and llvm-mca parses."""

    def one(m: re.Match) -> str:
        disp, inside = m.group(1), m.group(2)
        return f"[{inside}-{disp[1:]}]" if disp.startswith("-") else f"[{inside}+{disp}]"

    out = _GCC_ADDRESS.sub(one, body)
    # `call [QWORD PTR 32[r10]]` (an indirect call) comes out doubly bracketed.
    return re.sub(r"\[((?:BYTE|WORD|DWORD|QWORD) PTR \[[^\]]+\])\]", r"\1", out)


def extract_function(asm: str, name: str) -> str:
    """Pull one function's instructions and local labels out of compiler `-S` output."""
    lines = asm.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.split("#")[0].strip() == f"{name}:")
    except StopIteration:
        raise ToolError(f"function {name!r} not found in compiler output") from None
    body: list[str] = []
    for raw in lines[start + 1 :]:
        line = raw.split("#")[0].rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith((".size", ".Lfunc_end")):
            break
        if _LABEL.match(stripped):
            body.append(stripped)
        elif stripped.startswith("."):
            continue  # alignment and other directives
        else:
            body.append("\t" + " ".join(stripped.split(None, 1)))
    return "\n".join(body) + "\n"


def instruction_lines(body: str) -> list[str]:
    return [ln.strip() for ln in body.splitlines() if ln.strip() and not _LABEL.match(ln.strip())]


def wrap_function(body: str, name: str) -> str:
    return (
        f".intel_syntax noprefix\n.text\n.globl {name}\n.type {name}, @function\n"
        f"{name}:\n{body}.size {name}, .-{name}\n"
    )


def assemble(body: str, name: str = "f", relocations: bool = False) -> bytes:
    """Assemble a function body and return its machine code bytes. With
    `relocations`, references to other symbols are allowed and left as zeros
    (for code that will be linked into a project, not run alone)."""
    with tempfile.TemporaryDirectory() as d:
        src, obj = Path(d) / "f.s", Path(d) / "f.o"
        src.write_text(wrap_function(body, name))
        _run(["as", "--64", "-o", str(obj), str(src)])
        return _symbol_bytes(obj, name, relocations)


def relocations(body: str, name: str = "f") -> list[tuple[int, str]]:
    """(offset in the function, symbol) for each reference the assembled body
    makes to another symbol (calls, tail calls, data)."""
    with tempfile.TemporaryDirectory() as d:
        src, obj = Path(d) / "f.s", Path(d) / "f.o"
        src.write_text(wrap_function(body, name))
        _run(["as", "--64", "-o", str(obj), str(src)])
        with obj.open("rb") as f:
            elf = ELFFile(f)
            rela = elf.get_section_by_name(".rela.text")
            if rela is None:
                return []
            symtab = elf.get_section(rela["sh_link"])
            return [(r["r_offset"], symtab.get_symbol(r["r_info_sym"]).name) for r in rela.iter_relocations()]


def _symbol_bytes(obj: Path, name: str, relocations: bool = False) -> bytes:
    with obj.open("rb") as f:
        elf = ELFFile(f)
        if not relocations and any(s.name.startswith(".rela") for s in elf.iter_sections()):
            raise ToolError("function needs relocations (memory or call references are unsupported)")
        sym = next(s for s in elf.get_section_by_name(".symtab").iter_symbols() if s.name == name)
        text = elf.get_section(sym["st_shndx"])
        off = sym["st_value"]
        return text.data()[off : off + sym["st_size"]]


class McaReport(BaseModel):
    mcpu: str
    instructions: int
    uops: int
    rthroughput: float  # cycles per call when calls are independent
    latency: float  # cycles per call when each call depends on the previous result
    report: str  # full throughput report, for the model prompt


def _reg_alias(reg64: str, width: int) -> str:
    if width == 64:
        return reg64
    return {"rdi": "edi", "rsi": "esi", "rdx": "edx", "rcx": "ecx", "rax": "eax"}.get(reg64, reg64 + "d")


def _mca_input(body: str) -> str:
    # llvm-mca treats the block as a straight-line loop body (branches are not
    # followed), so drop the `ret` but keep labels so branch targets resolve.
    lines = [ln.strip() for ln in body.splitlines() if ln.strip() and ln.strip() != "ret"]
    return ".intel_syntax noprefix\n" + "\n".join(lines) + "\n"


def mca(body: str, target: Target, mcpu: str = DEFAULT_MCPU, iterations: int = 200) -> McaReport:
    base = _mca_input(body)
    tput = _run(["llvm-mca", f"-mcpu={mcpu}", f"-iterations={iterations}", "-bottleneck-analysis"], base)
    # Latency: feed the return value back into the first argument so iterations chain.
    w = 64 if bit_width(target.args[0]) == 64 or bit_width(target.ret) == 64 else 32
    feedback = f"mov {_reg_alias(ARG_REGS[0], w)}, {_reg_alias('rax', w)}\n"
    lat = _run(["llvm-mca", f"-mcpu={mcpu}", f"-iterations={iterations}"], base + feedback)

    def field(text: str, key: str) -> float:
        m = re.search(rf"^{key}:\s+([\d.]+)", text, re.M)
        if not m:
            raise ToolError(f"llvm-mca output missing {key!r}")
        return float(m.group(1))

    return McaReport(
        mcpu=mcpu,
        instructions=len([ln for ln in instruction_lines(body) if ln != "ret"]),
        uops=int(field(tput, "Total uOps") / iterations),
        rthroughput=round(field(tput, "Total Cycles") / iterations, 2),
        latency=round(field(lat, "Total Cycles") / iterations, 2),
        report=tput,
    )
