"""Baselines: what clang and gcc produce at -O3, plus static estimates."""

from __future__ import annotations

from pydantic import BaseModel

from .events import Baseline
from .targets import Target
from .toolchain import DEFAULT_MARCH, DEFAULT_MCPU, Compiler, McaReport, compile_to_asm, mca


class CompilerBaseline(BaseModel):
    compiler: Compiler
    asm: str
    mca: McaReport

    def to_event(self) -> Baseline:
        return Baseline(asm=self.asm, mca_rthroughput=self.mca.rthroughput, mca_latency=self.mca.latency)


def compute(
    target: Target, compiler: Compiler, march: str = DEFAULT_MARCH, mcpu: str = DEFAULT_MCPU
) -> CompilerBaseline:
    asm = compile_to_asm(target, compiler, march)
    return CompilerBaseline(compiler=compiler, asm=asm, mca=mca(asm, target, mcpu))
