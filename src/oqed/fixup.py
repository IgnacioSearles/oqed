"""Assembler errors in a candidate: where they are, what the rule is, and the
few that can be fixed without guessing what the code meant.

Models write x86 that mixes operand widths (`add r10, edx`), shifts by a
register other than cl, or puts three registers in an address. GNU as says
only "operand type mismatch for `add'", so the model learns nothing from it.
Here each error comes back with its line and the rule it breaks.

Fixes are applied only to lines the assembler rejects, and only where the
meaning is not in doubt:

- a shift or rotate by `rcx`/`ecx`/`cx` means by `cl`;
- the count/index operand of bzhi, shlx, shrx, sarx and bextr only has its low
  bits read, so it takes the destination's width;
- a store with an explicit size (`mov word ptr [rdx], eax`) keeps the low bits
  of the source, so the source register takes the store's width;
- a whole-register vector move (vmovdqu, vmovdqa, vmovups, vmovaps, vlddqu)
  moves the register's width whatever size the memory operand names;
- a label defined as `8f:` (the reference form) means `8:`.

Everything else (`add r64, r32`: zero- or sign-extend?) is left to the model.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .toolchain import wrap_function

_GPR64 = ["rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rbp", "rsp"] + [f"r{i}" for i in range(8, 16)]
_NAMES: dict[str, tuple[str, str, str, str]] = {}  # any name -> (64, 32, 16, 8) names
for r in _GPR64:
    if r.startswith("r") and r[1:].isdigit():
        names = (r, f"{r}d", f"{r}w", f"{r}b")
    elif r in ("rsi", "rdi", "rbp", "rsp"):
        names = (r, "e" + r[1:], r[1:], r[1:] + "l")
    else:
        names = (r, "e" + r[1:], r[1:], r[1] + "l")
    for n in names:
        _NAMES[n] = names
_WIDTH = {0: 64, 1: 32, 2: 16, 3: 8}
_PTR = {"byte": 8, "word": 16, "dword": 32, "qword": 64}
_SHIFTS = {"shl", "shr", "sar", "sal", "rol", "ror", "rcl", "rcr"}
_COUNT_LAST = {"bzhi", "shlx", "shrx", "sarx", "bextr"}
_VMOVES = {"vmovdqu", "vmovdqa", "vmovups", "vmovaps", "vlddqu"}


def _width(reg: str) -> int | None:
    names = _NAMES.get(reg)
    return _WIDTH[names.index(reg)] if names else None


def _resize(reg: str, bits: int) -> str:
    return _NAMES[reg][{64: 0, 32: 1, 16: 2, 8: 3}[bits]]


@dataclass
class AsmError:
    line: int  # 1-based, in the body
    text: str  # the instruction
    message: str

    def describe(self) -> str:
        hint = hint_for(self.message, self.text)
        return f"line {self.line} `{self.text}`: {self.message}" + (f" ({hint})" if hint else "")


def errors(body: str) -> list[AsmError]:
    """Every error GNU as reports in the body, with its line."""
    header = wrap_function("", "f").split("f:\n")[0].count("\n") + 1
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "f.s"
        src.write_text(wrap_function(body, "f"))
        proc = subprocess.run(
            ["as", "--64", "-o", str(Path(d) / "f.o"), str(src)], capture_output=True, text=True, check=False
        )
    lines = body.splitlines()
    out = []
    for m in re.finditer(r":(\d+): Error: (.*)", proc.stderr):
        n = int(m.group(1)) - header
        text = lines[n - 1].strip() if 1 <= n <= len(lines) else ""
        out.append(AsmError(n, text, m.group(2).strip()))
    return out


def hint_for(message: str, text: str) -> str:
    op = text.split(None, 1)[0].lower() if text else ""
    operands = [o.strip() for o in text.split(None, 1)[1].split(",")] if " " in text else []
    if "not a valid base/index" in message:
        if "rip" in text:
            return (
                "a RIP-relative address takes no index register: `lea r11, [rip+table]` first, "
                "then `[r11+rax*4]`"
            )
        return (
            "an address is base + index*{1,2,4,8} + displacement: at most two 64-bit registers, "
            "scale 1, 2, 4 or 8; add a third term with lea or add first"
        )
    if op in _SHIFTS and "mismatch" in message:
        return "a variable shift count must be in cl (`shl rax, cl`), or use shlx/shrx/sarx"
    if "mismatch" in message and any(re.fullmatch(r"-?(0x[0-9a-f]+|\d+)", o, re.I) for o in operands):
        return (
            "immediates are at most 32 bits, sign-extended; put a 64-bit constant in a register "
            "first (`mov r11, 0x...`) and use the register"
        )
    if "mismatch" in message:
        return (
            "operands must be the same width, except movzx/movsx/movsxd. A 32-bit register write "
            "zeroes the upper half, so a 32-bit value written by a 32-bit instruction can be used "
            "through its 64-bit name; to widen, use `mov r32, r32` (zero-extend) or movsxd "
            "(sign-extend), and load memory at its own width first"
        )
    if "REX" in message:
        return "ah, bh, ch, dh can't appear in an instruction that uses r8-r15, sil, dil, spl, bpl or 64 bits"
    if "no such instruction" in message:
        if op == "setcc" or op == "jcc" or op == "cmovcc":
            return "write the actual condition: sete, setne, setb, setl, ..."
        return "not an Intel-syntax mnemonic (no AT&T size suffixes like bsfl or movl)"
    if "already defined" in message:
        return "every label must be unique"
    if "fb label" in message or "is not defined" in message:
        return "a numeric label used as 1b/1f must exist before/after the jump"
    if "junk at end of line" in message:
        return "one instruction per line, Intel syntax, no AT&T operands"
    return ""


def _fix_line(text: str) -> str | None:
    """A rewritten instruction, when the error has one reading only."""
    if lb := re.fullmatch(r"(\d+)[fb]:", text.strip()):
        return f"{lb.group(1)}:"
    m = re.fullmatch(r"(\w+)\s+(.*)", text.strip())
    if not m:
        return None
    op, operands = m.group(1).lower(), [o.strip() for o in m.group(2).split(",")]
    regs = [o.lower() for o in operands]
    if op in _SHIFTS and len(operands) == 2 and regs[1] in ("rcx", "ecx", "cx"):
        return f"{op} {operands[0]}, cl"
    if op in _COUNT_LAST and len(operands) == 3 and _width(regs[0]) and _width(regs[2]):
        if _width(regs[0]) != _width(regs[2]) and _width(regs[0]) in (32, 64):
            return f"{op} {operands[0]}, {operands[1]}, {_resize(regs[2], _width(regs[0]))}"  # type: ignore[arg-type]
    if op in _VMOVES and len(operands) == 2:
        vec = next((r for r in regs if re.fullmatch(r"[xy]mm\d+", r)), None)
        mem = next((i for i, r in enumerate(regs) if "ptr" in r), None)
        if vec and mem is not None:
            size = "ymmword" if vec.startswith("y") else "xmmword"
            new = re.sub(r"^\w+\s+ptr", f"{size} ptr", operands[mem], flags=re.I)
            out = list(operands)
            out[mem] = new
            return f"{op} {', '.join(out)}" if new != operands[mem] else None
    if op == "mov" and len(operands) == 2 and _width(regs[1]):
        p = re.match(r"(byte|word|dword|qword)\s+ptr\b", regs[0])
        if p and _PTR[p.group(1)] < _width(regs[1]):  # type: ignore[operator]
            return f"mov {operands[0]}, {_resize(regs[1], _PTR[p.group(1)])}"
    return None


def fix(body: str) -> tuple[str, list[str]]:
    """The body with the unambiguous errors fixed, and what was changed."""
    lines = body.splitlines()
    notes = []
    for e in errors(body):
        new = _fix_line(e.text)
        if new is None or not 1 <= e.line <= len(lines):
            continue
        lines[e.line - 1] = new if new.endswith(":") else "\t" + new
        notes.append(f"`{e.text}` -> `{new}`")
    return "\n".join(lines) + "\n", notes
