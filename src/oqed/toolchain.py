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


def compile_to_asm(target: Target, compiler: Compiler, march: str = DEFAULT_MARCH) -> str:
    """Compile the target and return just its function body."""
    out = _run([compiler, *CFLAGS, f"-march={march}", "-S", "-o", "-", str(target.c_file)])
    return extract_function(out, target.function)


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


def assemble(body: str, name: str = "f") -> bytes:
    """Assemble a function body and return its machine code bytes."""
    with tempfile.TemporaryDirectory() as d:
        src, obj = Path(d) / "f.s", Path(d) / "f.o"
        src.write_text(wrap_function(body, name))
        _run(["as", "--64", "-o", str(obj), str(src)])
        return _symbol_bytes(obj, name)


def _symbol_bytes(obj: Path, name: str) -> bytes:
    with obj.open("rb") as f:
        elf = ELFFile(f)
        if any(s.name.startswith(".rela") for s in elf.iter_sections()):
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
