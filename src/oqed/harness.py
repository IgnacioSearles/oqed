"""Differential testing and timing on top of the Rust harness (`oqed_harness`).

Inputs follow the System V ABI as compilers actually rely on it: an argument
narrower than 64 bits only defines its low 32 bits (narrow types are sign- or
zero-extended to 32 by the caller, clang depends on this), and the upper 32
bits are undefined. We fill them with junk so candidates that read them fail.
Return values are compared only at the return type's width.
"""

from __future__ import annotations

import itertools
import os
import random
import statistics

import oqed_harness
from pydantic import BaseModel

from .events import Cycles
from .targets import IntType, Target, bit_width, is_signed

MAX_INPUTS = 60_000
_MASK64 = (1 << 64) - 1


def _wrap(v: int, t: IntType) -> int:
    """Interpret `v` as a value of type `t`."""
    w = bit_width(t)
    v &= (1 << w) - 1
    if is_signed(t) and v >> (w - 1):
        v -= 1 << w
    return v


def edge_values(t: IntType) -> list[int]:
    w = bit_width(t)
    lo, hi = (-(1 << (w - 1)), (1 << (w - 1)) - 1) if is_signed(t) else (0, (1 << w) - 1)
    vals = {0, 1, 2, 3, lo, lo + 1, hi, hi - 1, 0x55555555, 0xAAAAAAAA, 0x7F, 0x80, 0xFF, 0x100}
    vals |= {0x7FFF, 0x8000, 0xFFFF, 0x10000, -0x8000, -0x8001, -0x81, -0x80}
    for k in range(w):
        p = 1 << k
        vals |= {p, p - 1, p + 1, -p, -p - 1, -p + 1}
    return sorted({_wrap(v, t) for v in vals})


def to_register(v: int, t: IntType, junk: int) -> int:
    """Register contents for an argument of type `t` with value `v`; `junk`
    supplies the undefined upper 32 bits for types narrower than 64."""
    w = bit_width(t)
    if w == 64:
        return v & _MASK64
    low32 = _wrap(v, "i32" if is_signed(t) else "u32") & 0xFFFFFFFF
    return ((junk & 0xFFFFFFFF) << 32) | low32


def ret_value(rax: int, t: IntType) -> int:
    return _wrap(rax, t)


def make_inputs(target: Target, n_random: int = 4000, seed: int = 0) -> list[tuple[int, ...]]:
    """Typed argument tuples: edge-case combinations plus random values."""
    rng = random.Random(seed)
    edges = [edge_values(t) for t in target.args]
    combos = list(itertools.product(*edges))
    if len(combos) > MAX_INPUTS - n_random:
        combos = rng.sample(combos, MAX_INPUTS - n_random)
    rand = [tuple(_wrap(rng.getrandbits(bit_width(t)), t) for t in target.args) for _ in range(n_random)]
    return combos + rand


class Counterexample(BaseModel):
    args: dict[str, int]  # typed argument values, named a, b, c, ...
    expected: int
    got: int | None
    # Set when the mismatch only occurs with nonzero upper register bits,
    # which the ABI leaves undefined.
    needs_upper_bits: bool = False


class TestResult(BaseModel):
    passed: bool
    reason: str = ""
    counterexample: Counterexample | None = None
    inputs_tested: int = 0


def _arg_names(target: Target) -> list[str]:
    return [chr(ord("a") + i) for i in range(len(target.args))]


def _run(code: bytes, regs: list[list[int]]) -> oqed_harness.ExecResult:
    return oqed_harness.execute(code, regs, timeout_ms=2000)


def difftest(target: Target, reference: bytes, candidate: bytes, seed: int = 0) -> TestResult:
    """Run reference and candidate on the same inputs and compare results."""
    rng = random.Random(seed ^ 0x9E3779B9)
    typed = make_inputs(target, seed=seed)
    regs = [
        [to_register(v, t, rng.getrandbits(32)) for v, t in zip(args, target.args, strict=True)]
        for args in typed
    ]
    names = _arg_names(target)

    ref = _run(reference, regs)
    if ref.status != "ok":
        raise RuntimeError(f"reference failed in the harness: {ref.status}")
    cand = _run(candidate, regs)

    def cex(i: int, got: int | None) -> Counterexample:
        return Counterexample(
            args=dict(zip(names, typed[i], strict=True)),
            expected=ret_value(ref.outputs[i], target.ret),
            got=got,
        )

    if cand.status == "timeout":
        return TestResult(passed=False, reason="timed out", inputs_tested=cand.completed)
    if cand.status == "crashed":
        sig = {4: "SIGILL", 5: "SIGTRAP", 7: "SIGBUS", 8: "SIGFPE", 11: "SIGSEGV"}.get(cand.signal or 0)
        return TestResult(
            passed=False,
            reason=f"crashed ({sig or cand.signal or 'nonzero exit'})",
            counterexample=cex(cand.completed, None) if cand.completed < len(typed) else None,
            inputs_tested=cand.completed,
        )
    for i, (r, c, clob) in enumerate(zip(ref.outputs, cand.outputs, cand.clobbered, strict=False)):
        if clob:
            return TestResult(
                passed=False,
                reason=f"did not preserve {', '.join(clob)}",
                counterexample=cex(i, ret_value(c, target.ret)),
                inputs_tested=i,
            )
        if ret_value(r, target.ret) != ret_value(c, target.ret):
            ce = cex(i, ret_value(c, target.ret))
            # Does it also fail with the upper bits cleared? If not, the bug is
            # reading undefined upper register bits.
            clean = [to_register(v, t, 0) for v, t in zip(typed[i], target.args, strict=True)]
            rr, cc = _run(reference, [clean]), _run(candidate, [clean])
            if rr.status == cc.status == "ok" and ret_value(rr.outputs[0], target.ret) == ret_value(
                cc.outputs[0], target.ret
            ):
                ce.needs_upper_bits = True
            return TestResult(passed=False, reason="output mismatch", counterexample=ce, inputs_tested=i)
    return TestResult(passed=True, inputs_tested=len(typed))


def bench_cpu() -> int | None:
    """CPU to pin benchmarks to (`OQED_BENCH_CPU`), or None to not pin."""
    v = os.environ.get("OQED_BENCH_CPU")
    return int(v) if v else None


BENCH_INPUTS = 1024


def bench_inputs(target: Target, seed: int = 1, n: int = BENCH_INPUTS) -> list[list[int]]:
    """Fixed inputs for timing, the same for every candidate: half uniform over
    each argument's type, half small magnitudes (|v| < 1024), shuffled so
    branchy code sees unpredictable branches. Replayed often enough, a short
    sequence is learned by the branch predictor; see inline.BENCH_INPUTS."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        args = []
        for t in target.args:
            if i % 2:
                v = _wrap(rng.getrandbits(bit_width(t)), t)
            else:
                v = rng.randrange(-1023, 1024) if is_signed(t) else rng.randrange(1024)
            args.append(to_register(v, t, 0))
        out.append(args)
    rng.shuffle(out)
    return out


def measure(code: bytes, target: Target, latency: bool = True, reps: int = 101, iters: int = 4096) -> Cycles:
    """TSC ticks per call (call overhead included), median and interquartile
    range over `reps` samples, each averaging `iters` calls over `bench_inputs`."""
    r = oqed_harness.bench(
        code, bench_inputs(target), iters=iters, reps=reps, latency=latency, cpu=bench_cpu()
    )
    if r.status != "ok":
        raise RuntimeError(f"benchmark failed: {r.status}")
    q1, med, q3 = statistics.quantiles(r.samples, n=4)
    return Cycles(median=round(med, 3), spread=round(q3 - q1, 3))
