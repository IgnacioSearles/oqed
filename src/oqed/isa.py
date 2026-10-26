"""ISA fairness check: a candidate may only use instructions the baseline
compilers were allowed to use (`-march`). Neither `as` nor `llvm-mc` enforces
this, so we decode the machine code and check each instruction's CPUID
features."""

from __future__ import annotations

from iced_x86 import CpuidFeature, Decoder, Formatter, FormatterSyntax

_FEATURE_NAMES = {v: k for k, v in vars(CpuidFeature).items() if not k.startswith("_")}

# x86-64 psABI microarchitecture levels, as iced CpuidFeature names.
_BASE = {
    "INTEL8086",
    "INTEL186",
    "INTEL286",
    "INTEL386",
    "INTEL486",
    "X64",
    "CMOV",
    "CX8",
    "FPU",
    "MMX",
    "SSE",
    "SSE2",
    "FXSR",
    "MULTIBYTENOP",
}
_V2 = _BASE | {"SSE3", "SSSE3", "SSE4_1", "SSE4_2", "POPCNT", "CMPXCHG16B"}
_V3 = _V2 | {"AVX", "AVX2", "BMI1", "BMI2", "F16C", "FMA", "LZCNT", "MOVBE"}

MARCH_FEATURES: dict[str, set[str]] = {"x86-64": _BASE, "x86-64-v2": _V2, "x86-64-v3": _V3}


def isa_violations(code: bytes, march: str) -> list[str]:
    """Instructions in `code` that need CPU features outside `march`, formatted
    as "insn (FEATURE)"; empty if the code is within the allowed ISA."""
    allowed = MARCH_FEATURES[march]
    fmt = Formatter(FormatterSyntax.INTEL)
    bad = []
    for insn in Decoder(64, code):
        missing = [_FEATURE_NAMES[f] for f in insn.cpuid_features() if _FEATURE_NAMES[f] not in allowed]
        if missing:
            bad.append(f"{fmt.format(insn)} ({', '.join(missing)})")
    return bad
