"""SMT equivalence checking with angr (VEX lifting, symbolic execution) and Z3.

`check` decides whether a candidate is equivalent to the reference for every
input the System V ABI allows:

- Argument registers are shared symbolic values. For arguments narrower than
  64 bits only the low 32 bits are defined (narrow types are sign/zero
  extended to 32 bits); the upper bits are unconstrained.
- Every other general-purpose register also starts as a shared symbolic value,
  so code that reads garbage registers is not equivalent.
- Outputs that must match: rax at the return type's width, and the
  callee-saved registers and rsp must be restored.

Results are `equivalent`, `not_equivalent` (with a counterexample that has been
replayed on the real CPU), or `unknown`. Anything outside what the model can
soundly decide is `unknown`, never `equivalent`: loops, memory accesses other
than the stack, indirect jumps, instructions with architecturally undefined
results, lifting failures and timeouts.

Proofs are relative to VEX's x86 semantics. Every candidate has also passed
differential testing on hardware, and counterexamples are cross-checked there.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from concurrent.futures.process import BrokenProcessPool
from typing import Literal

from iced_x86 import Decoder, FlowControl, Mnemonic, OpKind, Register
from pydantic import BaseModel

from . import harness
from .targets import ARG_REGS, Target, bit_width, is_signed

Status = Literal["equivalent", "not_equivalent", "unknown"]

LOAD_ADDR = 0x400000
STACK_TOP = 0x7FFF0000
RETURN_SENTINEL = 0x5E7700
MAX_STEPS = 64
SOLVER_TIMEOUT_MS = 20_000

GPRS = ["rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rbp", "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15"]
CALLEE_SAVED = ["rbx", "rbp", "r12", "r13", "r14", "r15"]

# Results architecturally undefined for some inputs (destination of bsf/bsr
# when the source is zero): VEX picks one behaviour, hardware may differ.
_UNDEFINED_RESULT = {Mnemonic.BSF, Mnemonic.BSR}


class VerifyResult(BaseModel):
    status: Status
    reason: str = ""
    counterexample: dict[str, int] | None = None  # typed argument values
    registers: dict[str, int] | None = None  # full register values of the counterexample
    elapsed_ms: float = 0.0


def unsupported(code: bytes) -> str | None:
    """Why `code` is outside what the verifier can soundly decide, or None."""
    end = len(code)
    for insn in Decoder(64, code, ip=0):
        if insn.mnemonic in _UNDEFINED_RESULT:
            return "uses bsf/bsr, whose result is undefined for zero input"
        flow = insn.flow_control
        if flow in (FlowControl.INDIRECT_BRANCH, FlowControl.INDIRECT_CALL, FlowControl.CALL):
            return "contains a call or indirect jump"
        if flow in (FlowControl.UNCONDITIONAL_BRANCH, FlowControl.CONDITIONAL_BRANCH):
            target = insn.near_branch_target
            if target <= insn.ip:
                return "contains a loop (backward branch); only loop-free code is proven"
            if target > end:
                return "branches outside the function"
        if insn.mnemonic != Mnemonic.LEA:
            for op in range(insn.op_count):
                if insn.op_kind(op) == OpKind.MEMORY and (
                    insn.memory_base != Register.RSP or insn.memory_index != Register.NONE
                ):
                    return "accesses memory other than the stack"
    return None


def _run_symbolic(code: bytes, regs0: dict):
    import angr
    from angr import claripy

    proj = angr.load_shellcode(code, arch="amd64", load_address=LOAD_ADDR)
    proj.hook(RETURN_SENTINEL, angr.SIM_PROCEDURES["stubs"]["PathTerminator"]())
    state = proj.factory.blank_state(
        addr=LOAD_ADDR,
        add_options={
            angr.options.SYMBOL_FILL_UNCONSTRAINED_MEMORY,
            angr.options.SYMBOL_FILL_UNCONSTRAINED_REGISTERS,
        },
    )
    for r in GPRS:
        setattr(state.regs, r, regs0[r])
    state.regs.rsp = claripy.BVV(STACK_TOP, 64)
    state.memory.store(STACK_TOP, claripy.BVV(RETURN_SENTINEL, 64), endness="Iend_LE")
    simgr = proj.factory.simgr(state)
    simgr.run(n=MAX_STEPS)
    if simgr.errored:
        return None, f"symbolic execution failed: {simgr.errored[0].error}"
    if simgr.active:
        return None, "did not finish within the step limit"
    if simgr.unconstrained:
        return None, "control flow escapes (return address or jump target is not fixed)"
    done = simgr.deadended
    if not done or any(s.addr != RETURN_SENTINEL for s in done):
        return None, "a path does not return to the caller"
    return done, None


def _preconditions(target: Target, regs0: dict) -> list:
    from angr import claripy

    pre = []
    for i, t in enumerate(target.args):
        w, reg = bit_width(t), regs0[ARG_REGS[i]]
        if w < 32:
            ext = claripy.SignExt if is_signed(t) else claripy.ZeroExt
            pre.append(reg[31:0] == ext(32 - w, reg[w - 1 : 0]))
    return pre


def check_sync(
    target: Target, reference: bytes, candidate: bytes, timeout_ms: int = SOLVER_TIMEOUT_MS
) -> VerifyResult:
    """Decide equivalence in this process (CPU-bound; see `Verifier` for a pool)."""
    from angr import claripy

    t0 = time.perf_counter()

    def done(status: Status, reason: str = "", **kw) -> VerifyResult:
        return VerifyResult(status=status, reason=reason, elapsed_ms=(time.perf_counter() - t0) * 1000, **kw)

    if not target.loop_free:
        return done("unknown", "target is not loop-free")
    for label, code in (("reference", reference), ("candidate", candidate)):
        if why := unsupported(code):
            return done("unknown", f"{label} {why}")

    regs0 = {r: claripy.BVS(f"init_{r}", 64, explicit_name=True) for r in GPRS}
    ref_states, err = _run_symbolic(reference, regs0)
    if err:
        return done("unknown", f"reference: {err}")
    cand_states, err = _run_symbolic(candidate, regs0)
    if err:
        return done("unknown", f"candidate: {err}")

    w = bit_width(target.ret)
    pre = _preconditions(target, regs0)
    for rs in ref_states:
        for cs in cand_states:
            s = claripy.Solver(timeout=timeout_ms)
            s.add(pre)
            s.add(rs.solver.constraints)
            s.add(cs.solver.constraints)
            differs = [rs.regs.rax[w - 1 : 0] != cs.regs.rax[w - 1 : 0]]
            differs += [getattr(cs.regs, r) != regs0[r] for r in CALLEE_SAVED]
            differs.append(cs.regs.rsp != STACK_TOP + 8)
            s.add(claripy.Or(*differs))
            try:
                sat = s.satisfiable()
            except claripy.ClaripySolverInterruptError:
                return done("unknown", "solver timed out")
            if not sat:
                continue
            regs = {r: s.eval(regs0[r], 1)[0] for r in GPRS}
            return _confirm_on_hardware(target, reference, candidate, regs, done)
    return done("equivalent")


def _confirm_on_hardware(target, reference, candidate, regs, done) -> VerifyResult:
    """Replay an SMT counterexample on the CPU. A counterexample the hardware
    does not reproduce means the semantic model and the CPU disagree, so the
    result is unknown rather than a rejection based on the model alone."""
    import oqed_harness

    names = [chr(ord("a") + i) for i in range(len(target.args))]
    args = [regs[ARG_REGS[i]] for i in range(len(target.args))]
    typed = {n: harness._wrap(v, t) for n, v, t in zip(names, args, target.args, strict=True)}
    ref = oqed_harness.execute(reference, [args])
    cand = oqed_harness.execute(candidate, [args])
    shown = {ARG_REGS[i]: regs[ARG_REGS[i]] for i in range(len(target.args))}
    upper = [ARG_REGS[i] for i, t in enumerate(target.args) if bit_width(t) < 64 and regs[ARG_REGS[i]] >> 32]
    note = f" (with nonzero undefined upper bits in {', '.join(upper)})" if upper else ""
    if cand.status != "ok":
        return done("not_equivalent", f"crashes ({cand.status})", counterexample=typed, registers=shown)
    if cand.clobbered[0]:
        return done(
            "not_equivalent",
            f"does not preserve {', '.join(cand.clobbered[0])}",
            counterexample=typed,
            registers=shown,
        )
    r, c = harness.ret_value(ref.outputs[0], target.ret), harness.ret_value(cand.outputs[0], target.ret)
    if r != c:
        return done(
            "not_equivalent", f"returns {c}, expected {r}{note}", counterexample=typed, registers=shown
        )
    # The harness fixes the non-argument registers, so the SMT model's choice
    # for them (e.g. a garbage register the candidate reads) is not replayed.
    return done(
        "unknown",
        "SMT found a difference the hardware replay did not reproduce "
        "(possibly depends on non-argument registers)",
        counterexample=typed,
        registers=shown,
    )


def _worker_init() -> None:
    for name in ("angr", "cle", "pyvex", "claripy"):
        logging.getLogger(name).setLevel(logging.ERROR)


class Verifier:
    """Process pool for verification with a wall-clock timeout per check."""

    def __init__(self, workers: int = 4, timeout_s: float = 60.0):
        self.workers, self.timeout_s = workers, timeout_s
        self._lock = threading.Lock()
        self._pool = self._new_pool()

    def _new_pool(self) -> ProcessPoolExecutor:
        # spawn: the orchestrator is multithreaded, so forking it is unsafe.
        ctx = multiprocessing.get_context("spawn")
        return ProcessPoolExecutor(self.workers, mp_context=ctx, initializer=_worker_init)

    def _restart(self, broken: ProcessPoolExecutor) -> None:
        # A check that ignores its timeout keeps a worker busy forever; the
        # executor has no public way to kill workers, so terminate them. That
        # breaks every other check running in the pool too; those see
        # BrokenProcessPool and retry on the new pool. Only the first caller
        # for a given pool replaces it.
        with self._lock:
            if self._pool is not broken:
                return
            for p in list(getattr(broken, "_processes", {}).values()):
                p.kill()
            broken.shutdown(wait=False, cancel_futures=True)
            self._pool = self._new_pool()

    def check(self, target: Target, reference: bytes, candidate: bytes) -> VerifyResult:
        for _ in range(3):
            pool = self._pool
            try:
                fut = pool.submit(check_sync, target, reference, candidate, int(self.timeout_s * 1000 * 0.8))
                return fut.result(timeout=self.timeout_s)
            except FutureTimeout:
                self._restart(pool)
                return VerifyResult(
                    status="unknown", reason="verification timed out", elapsed_ms=self.timeout_s * 1000
                )
            except BrokenProcessPool:
                self._restart(pool)  # killed alongside another check that timed out
        return VerifyResult(status="unknown", reason="verifier workers kept crashing", elapsed_ms=0.0)

    async def check_async(self, target: Target, reference: bytes, candidate: bytes) -> VerifyResult:
        return await asyncio.to_thread(self.check, target, reference, candidate)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
