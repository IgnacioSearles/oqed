"""Baselines: what clang and gcc produce at -O3, plus static estimates."""

from __future__ import annotations

from pydantic import BaseModel

from .events import Baseline
from .targets import Target
from .toolchain import DEFAULT_MARCH, DEFAULT_MCPU, Compiler, McaReport, ToolError, compile_to_asm, mca


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
    try:
        report = mca(asm, target, mcpu)
    except ToolError as e:
        # The estimate only informs the prompt; a listing llvm-mca can't read
        # must not stop the search.
        report = McaReport(
            mcpu=mcpu, instructions=0, uops=0, rthroughput=0.0, latency=0.0, report=str(e)[-500:]
        )
    return CompilerBaseline(compiler=compiler, asm=asm, mca=report)
