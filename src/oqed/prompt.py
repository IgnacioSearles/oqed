"""Prompts and reply parsing.

Every model gets the same plain-text contract (Muse Spark Open on Baseten has
no structured output). The stable part of the prompt (rules, C source,
baselines) comes first so providers can reuse cached input across calls; the
search state (best candidates, recent failures) comes last.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .targets import ARG_REGS, Target, bit_width, c_signature

SYSTEM = """You are an expert x86-64 performance engineer acting as a superoptimizer.
You rewrite small, hot functions into faster assembly that is exactly equivalent
to the C source for every possible input.

Rules for the code you write:
- Intel syntax without `%` prefixes (GNU as `.intel_syntax noprefix`), function body only:
  instructions and local labels like `1:` or `.Lskip:`. No directives, no function label.
- System V AMD64 ABI. Integer arguments arrive in rdi, rsi, rdx, rcx; the result goes in rax.
- For arguments narrower than 64 bits, only the low 32 bits of the register are defined
  (8/16-bit types are already sign/zero-extended to 32 bits). The upper 32 bits are garbage.
- For results narrower than 64 bits, only the low bits of rax matching the return type matter.
- Preserve rbx, rbp, r12-r15 and rsp. rax, rcx, rdx, rsi, rdi, r8-r11 are free scratch.
- No memory accesses, no calls, no system instructions. End with `ret`.
- Allowed ISA: x86-64-v3 (up to AVX2, BMI1, BMI2, LZCNT, POPCNT, MOVBE, FMA, F16C).
  Not allowed: AVX-512, PCLMULQDQ, AES, SHA, ADX, or anything newer.

Optimize for latency of one call (the critical path from arguments to rax), then
for fewer instructions/uops. Correctness is checked by testing and an SMT proof,
so subtle mistakes (edge cases like 0, INT_MIN, all-ones; flags; shift counts that
wrap; reading the garbage upper half of a 32-bit argument) get caught and wasted.

Reply in exactly this format:
<explanation>One sentence naming the trick.</explanation>
<asm>
...instructions...
</asm>"""


def _arg_reg(i: int, t: str) -> str:
    return ARG_REGS[i] if bit_width(t) == 64 else "e" + ARG_REGS[i][1:]  # rdi -> edi


def _type_notes(target: Target) -> str:
    args = ", ".join(f"{chr(ord('a') + i)}: {t} in {_arg_reg(i, t)}" for i, t in enumerate(target.args))
    return f"Arguments: {args}. Return: {target.ret} in {'rax' if bit_width(target.ret) == 64 else 'eax'}."


@dataclass
class BaselineInfo:
    compiler: str
    asm: str
    mca_latency: float
    mca_rthroughput: float
    measured_latency: float | None


@dataclass
class CandidateInfo:
    asm: str
    explanation: str
    measured_latency: float | None = None
    failure: str | None = None  # stage: reason (+ counterexample)


def target_context(target: Target, baselines: list[BaselineInfo], mca_report: str) -> str:
    parts = [
        f"Function to optimize:\n```c\n{target.c_source.strip()}\n```",
        f"Signature: `{c_signature(target)}`. {_type_notes(target)}",
    ]
    for b in baselines:
        m = f", measured {b.measured_latency:.2f} TSC ticks/call" if b.measured_latency else ""
        parts.append(
            f"{b.compiler} -O3 -march=x86-64-v3 output (llvm-mca: latency {b.mca_latency}, "
            f"throughput {b.mca_rthroughput} cycles{m}):\n```asm\n{b.asm.rstrip()}\n```"
        )
    parts.append(f"llvm-mca report for the clang output:\n```\n{mca_report.strip()}\n```")
    return "\n\n".join(parts)


# One is picked per volume-tier call so parallel calls explore different directions
# instead of converging on the same answer.
HINTS = [
    "Consider BMI1/BMI2 instructions (andn, blsi, blsr, blsmsk, bzhi, shlx/shrx/sarx, rorx, pdep/pext).",
    "Consider flag tricks: setcc, cmovcc, sbb/adc to materialize masks, carry and overflow flags.",
    "Consider lea for fused add/shift/scale arithmetic.",
    "Consider widening to 64-bit arithmetic so overflow and edge cases disappear.",
    "Consider count instructions (lzcnt, tzcnt, popcnt) and what they return for zero.",
    "Consider Hacker's Delight identities that replace multi-step sequences.",
    "Minimize the number of instructions on the critical path; independent work is nearly free.",
    "Consider whether fewer, cheaper uops beat a shorter dependency chain here.",
    "Consider multiplication or division-by-constant tricks.",
    "Consider SSE/AVX2 scalar or SWAR approaches if they shorten the chain.",
]


def search_state(
    best: list[CandidateInfo], failures: list[CandidateInfo], reasoning: bool, hint: str | None = None
) -> str:
    parts = []
    if best:
        parts.append("Best verified candidates so far (fastest first):")
        for c in best:
            lat = f"{c.measured_latency:.2f} ticks/call" if c.measured_latency else "unmeasured"
            parts.append(f"- {c.explanation} ({lat})\n```asm\n{c.asm.rstrip()}\n```")
    if failures:
        parts.append("Recent failed attempts (do not repeat these mistakes):")
        for c in failures:
            parts.append(f"- {c.explanation} -> {c.failure}\n```asm\n{c.asm.rstrip()}\n```")
    if reasoning:
        parts.append(
            "The search has stalled: recent attempts did not beat the best. Step back and analyse the\n"
            "critical path and the bottleneck report. Then propose a substantially different approach,\n"
            "not a small tweak of the candidates above."
        )
    elif best:
        parts.append(
            "Improve on the best candidate: shorten its critical path or remove instructions. "
            "Your answer must differ from every candidate shown above."
        )
    else:
        parts.append("Write a faster equivalent of the compiler output.")
    if hint and not reasoning:
        parts.append(f"Direction for this attempt: {hint}")
    return "\n\n".join(parts)


def build_messages(context: str, state: str) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"{context}\n\n{state}"},
    ]


class ParseError(ValueError):
    pass


_DIRECTIVE = re.compile(r"^\.(?!L\w*:)")


def parse_reply(text: str, function: str) -> tuple[str, str]:
    """Return (explanation, asm body) from a model reply."""
    m = re.search(r"<asm>(.*?)(?:</asm>|$)", text, re.S)
    if not m:
        m = re.search(r"```(?:asm|nasm|x86asm|gas|s)?\n(.*?)```", text, re.S)
    if not m:
        raise ParseError("no <asm> block in reply")
    e = re.search(r"<explanation>(.*?)</explanation>", text, re.S)
    explanation = " ".join(e.group(1).split()) if e else ""

    lines = []
    for raw in m.group(1).replace("```", "").splitlines():
        line = re.split(r"\s(?:#|//|;)|^(?:#|//|;)", raw, maxsplit=1)[0].strip()
        if not line or _DIRECTIVE.match(line):
            continue
        if line == f"{function}:":
            continue  # the model repeated the function label
        lines.append(line if line.endswith(":") else f"\t{line}")
    if not lines:
        raise ParseError("empty <asm> block")
    return explanation, "\n".join(lines) + "\n"
