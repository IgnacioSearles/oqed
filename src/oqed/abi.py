"""Static System V ABI checks for candidates that are whole functions.

A candidate that calls other functions must follow the calling convention,
and the project's tests may never notice when it doesn't: a value kept in a
caller-saved register across a call survives until a callee happens to use
that register, and a misaligned stack only crashes when a callee uses aligned
SSE stores. So these are checked on the machine code, over every path:

- a caller-saved register (rcx, rdx, rsi, rdi, r8-r11, xmm/ymm) read after a
  `call` and before being written again (rax and xmm0 hold the result);
- a `call` with rsp not 16-byte aligned;
- a callee-saved register (rbx, rbp, r12-r15) not holding its entry value at
  a `ret`, or rsp not back where it started.

The stack is tracked as pushes, pops and `sub`/`add rsp, imm`, with each slot
remembering which register's entry value it holds. When the code does
something else to rsp (`and rsp`, `mov rsp, rbp`, `leave`), the stack checks
for that path are dropped rather than guessed: this check rejects code that is
wrong, never code it doesn't understand.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import iced_x86 as iced

from .toolchain import assemble, relocations

_R = iced.Register
_FULL = iced.RegisterExt.full_register
CALLEE_SAVED = {_R.RBX, _R.RBP, _R.R12, _R.R13, _R.R14, _R.R15}
CALLER_SAVED = {_R.RCX, _R.RDX, _R.RSI, _R.RDI, _R.R8, _R.R9, _R.R10, _R.R11} | {
    getattr(_R, f"ZMM{i}") for i in range(1, 32)
}
_RESULT = {_R.RAX, _R.ZMM0}
_READS = {
    iced.OpAccess.READ,
    iced.OpAccess.READ_WRITE,
    iced.OpAccess.COND_READ,
    iced.OpAccess.READ_COND_WRITE,
}
_WRITES = {iced.OpAccess.WRITE, iced.OpAccess.READ_WRITE}
_DEAD = "dead"  # a stack slot holding a register a call left undefined
_NAMES = {v: k.lower() for k, v in vars(_R).items() if isinstance(v, int) and not k.startswith("_")}


@dataclass(frozen=True)
class _State:
    dead: frozenset = frozenset()  # caller-saved registers a call left undefined
    # Stack slots pushed since entry, innermost last; each holds the entry
    # value of a callee-saved register, or None. `None` for the whole tuple:
    # the stack is not tracked on this path.
    stack: tuple | None = ()
    modified: frozenset = frozenset()  # callee-saved registers not holding their entry value


@dataclass
class _Walk:
    insns: list
    at: dict = field(default_factory=dict)  # ip -> index
    problems: list = field(default_factory=list)

    def report(self, insn, msg: str) -> None:
        text = f"{msg} ({insn})"
        if text not in self.problems:
            self.problems.append(text)


def _join(a: _State, b: _State) -> _State:
    stack = a.stack if a.stack == b.stack else None
    return _State(a.dead | b.dead, stack, a.modified | b.modified)


def _depth(stack: tuple | None) -> int | None:
    return None if stack is None else 8 * len(stack)


def _step(w: _Walk, insn, s: _State, info) -> _State:
    regs = [(_FULL(r.register), r.access) for r in info.used_registers()]
    m = insn.mnemonic
    # vzeroupper/vzeroall only clear vector registers (iced lists them as read).
    if m in (iced.Mnemonic.VZEROUPPER, iced.Mnemonic.VZEROALL):
        regs = [
            (r, iced.OpAccess.WRITE if m == iced.Mnemonic.VZEROALL else iced.OpAccess.NONE) for r, _ in regs
        ]
    for reg, acc in regs:
        # Pushing a dead register (to align the stack) reads nothing that matters.
        if acc in _READS and reg in s.dead and reg != _R.RSP and m != iced.Mnemonic.PUSH:
            w.report(
                insn,
                f"reads {_NAMES[reg]} after a call, which may have changed it: keep values needed "
                "after a call in rbx, rbp, r12-r15 (saved on entry) or in a stack slot",
            )
    dead, stack, modified = set(s.dead), s.stack, set(s.modified)
    if m == iced.Mnemonic.CALL:
        if stack is not None and (8 + _depth(stack)) % 16:
            depth = _depth(stack)
            w.report(
                insn,
                f"calls with rsp not 16-byte aligned: {depth} bytes pushed or allocated since entry, "
                f"and the return address makes it {depth + 8}; it must be a multiple of 16",
            )
        dead = (dead | CALLER_SAVED) - _RESULT
        return _State(frozenset(dead), stack, frozenset(modified))
    if m == iced.Mnemonic.PUSH and stack is not None:
        r = _FULL(insn.op0_register) if insn.op0_kind == iced.OpKind.REGISTER else None
        slot = _DEAD if r in dead else r if r in CALLEE_SAVED and r not in modified else None
        stack = stack + (slot,)
    elif m == iced.Mnemonic.POP and stack is not None:
        r = _FULL(insn.op0_register) if insn.op0_kind == iced.OpKind.REGISTER else None
        if not stack:
            w.report(insn, "pops more than it pushed")
            stack = None
        else:
            slot, stack = stack[-1], stack[:-1]
            if r is not None:
                (dead.add if slot == _DEAD else dead.discard)(r)
                if r in CALLEE_SAVED:
                    (modified.discard if slot == r else modified.add)(r)
        return _State(frozenset(dead), stack, frozenset(modified))
    elif m in (iced.Mnemonic.SUB, iced.Mnemonic.ADD) and _is_rsp_imm(insn) and stack is not None:
        n = insn.immediate(1)
        if n % 8:
            stack = None
        elif m == iced.Mnemonic.SUB:
            stack = stack + (None,) * (n // 8)
        elif n // 8 > len(stack):
            w.report(insn, "frees more stack than it allocated")
            stack = None
        else:
            stack = stack[: len(stack) - n // 8]
        return _State(frozenset(dead), stack, frozenset(modified))
    elif any(reg == _R.RSP and acc in _WRITES for reg, acc in regs) and m not in (
        iced.Mnemonic.PUSH,
        iced.Mnemonic.POP,
        iced.Mnemonic.RET,
    ):
        stack = None  # rsp changed some other way: stop tracking the stack
    for reg, acc in regs:
        if acc not in _WRITES or reg == _R.RSP:
            continue
        if reg in CALLEE_SAVED:
            modified.add(reg)
        # A write of 8 or 16 bits keeps the rest of the register undefined.
        if acc == iced.OpAccess.WRITE and _writes_whole(insn, reg):
            dead.discard(reg)
    return _State(frozenset(dead), stack, frozenset(modified))


def _is_rsp_imm(insn) -> bool:
    return (
        insn.op0_kind == iced.OpKind.REGISTER
        and insn.op0_register == _R.RSP
        and insn.op1_kind in (iced.OpKind.IMMEDIATE8TO64, iced.OpKind.IMMEDIATE32TO64)
    )


def _writes_whole(insn, reg) -> bool:
    # 32- and 64-bit GPR writes define the whole register; vector registers
    # are treated as defined by any write (the ABI result is xmm0 either way).
    for i in range(insn.op_count):
        if insn.op_kind(i) == iced.OpKind.REGISTER and _FULL(insn.op_register(i)) == reg:
            r = insn.op_register(i)
            return not (iced.RegisterExt.is_gpr8(r) or iced.RegisterExt.is_gpr16(r))
    return True  # implicit operand (e.g. `cpuid`, `mul`): full width


def violations(body: str) -> list[str]:
    """ABI problems in a whole-function candidate, as readable messages."""
    code = assemble(body, relocations=True)
    insns = list(iced.Decoder(64, code))
    w = _Walk(insns, {insn.ip: i for i, insn in enumerate(insns)})
    # Branches whose target is another symbol (tail calls) carry a relocation.
    refs = [off for off, _ in relocations(body)]
    external = {i for i, insn in enumerate(insns) if any(insn.ip <= off < insn.next_ip for off in refs)}
    factory = iced.InstructionInfoFactory()
    infos = [factory.info(insn) for insn in insns]
    states: dict[int, _State] = {0: _State()}
    todo = [0]
    while todo:
        i = todo.pop()
        insn = insns[i]
        out = _step(w, insn, states[i], infos[i])
        fc, succ = insn.flow_control, []
        if fc == iced.FlowControl.RETURN:
            if out.stack:
                w.report(insn, "returns with the stack not restored")
            # Without a tracked stack the pops can't be matched to pushes.
            for reg in sorted(out.modified & CALLEE_SAVED) if out.stack is not None else []:
                w.report(insn, f"returns without restoring {_NAMES[reg]}, which the caller relies on")
        elif fc in (iced.FlowControl.NEXT, iced.FlowControl.CALL, iced.FlowControl.INDIRECT_CALL):
            succ.append(i + 1)
        elif fc in (iced.FlowControl.CONDITIONAL_BRANCH, iced.FlowControl.UNCONDITIONAL_BRANCH):
            if fc == iced.FlowControl.CONDITIONAL_BRANCH:
                succ.append(i + 1)
            if i in external:
                _tail_call(w, insn, out)
            elif insn.near_branch_target in w.at:
                succ.append(w.at[insn.near_branch_target])
        # Indirect jumps (jump tables) aren't followed: nothing is checked past them.
        for j in succ:
            if j >= len(insns):
                continue
            new = out if j not in states else _join(states[j], out)
            if states.get(j) != new:
                states[j] = new
                todo.append(j)
    return w.problems


def _tail_call(w: _Walk, insn, s: _State) -> None:
    # Leaving through `jmp other`: the callee returns to our caller, so the
    # stack and callee-saved registers must already be as on entry.
    if s.stack:
        w.report(insn, "tail-calls with the stack not restored")
    for reg in sorted(s.modified & CALLEE_SAVED) if s.stack is not None else []:
        w.report(insn, f"tail-calls without restoring {_NAMES[reg]}")
