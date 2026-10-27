"""The search loop: ask models for candidates, push each through the filter
pipeline (parse, assemble, ISA, differential test, llvm-mca estimate,
measured latency), and feed the best results and failures back into the
next prompts. When the search stalls, the reasoning tier is called.

Until the SMT verifier lands, surviving candidates are `tested_only`; this
module never claims a proof.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from . import harness, prompt
from .baseline import compute as compute_baseline
from .events import (
    RUNS_DIR,
    BaselineReady,
    CandidateBenchmarked,
    CandidateProposed,
    CandidateRejected,
    CandidateVerified,
    Cycles,
    EventLog,
    FunctionSummary,
    ModelTier,
    NewBest,
    RejectStage,
    RunFinished,
    RunStarted,
    SearchStalled,
    TimingBreakdown,
)
from .isa import forbidden_instructions, isa_violations
from .providers import Completion, ProviderError
from .targets import Target, c_signature
from .toolchain import DEFAULT_MARCH, DEFAULT_MCPU, ToolError, assemble, mca


class Model(Protocol):
    model: str

    async def complete(
        self, messages: list[dict], effort: str | None = None, max_tokens: int = ...
    ) -> Completion: ...


@dataclass
class SearchConfig:
    candidates: int = 24  # model calls per function
    concurrency: int = 8
    stall_after: int = 8  # evaluated candidates without improvement before calling the reasoning tier
    volume_effort: str = "low"
    reasoning_effort: str = "high"
    population: int = 3  # best candidates shown to the model
    failures_shown: int = 6
    min_gain: float = 0.02  # a candidate must be at least 2% faster than the bar it has to beat
    march: str = DEFAULT_MARCH
    mcpu: str = DEFAULT_MCPU


def _faster(c: Cycles, bar: Cycles, min_gain: float) -> bool:
    """Faster by a margin that clears both the noise and `min_gain`."""
    return c.median < bar.median * (1 - min_gain) and c.median + c.spread / 2 < bar.median - bar.spread / 2


def _normalize(asm: str) -> str:
    return "\n".join(" ".join(ln.lower().split()) for ln in asm.splitlines() if ln.strip())


def _describe_failure(stage: str, reason: str, ce: harness.Counterexample | None) -> str:
    text = f"{stage}: {reason}"
    if ce:
        args = ", ".join(f"{k}={v}" for k, v in ce.args.items())
        text += f" for {args} (expected {ce.expected}, got {ce.got})"
        if ce.needs_upper_bits:
            text += "; it only fails when the undefined upper 32 bits of an argument are nonzero"
    return text


@dataclass
class _Best:
    candidate_id: str
    asm: str
    explanation: str
    cycles: Cycles


@dataclass
class FunctionSearch:
    target: Target
    volume: Model
    reasoning: Model
    config: SearchConfig
    log: EventLog
    bench_lock: asyncio.Lock
    best: list[_Best] = field(default_factory=list)
    failures: deque = field(default_factory=lambda: deque(maxlen=6))
    seen: set[str] = field(default_factory=set)
    stall: int = 0
    proposed: int = 0
    reasoning_in_flight: bool = False

    def emit(self, event_cls, **kw) -> None:
        self.log.emit(event_cls(run_id=self.log.run_id, function_id=self.target.name, **kw))

    async def _measure(self, code: bytes) -> Cycles:
        async with self.bench_lock:
            return await asyncio.to_thread(harness.measure, code, self.target, True)

    async def setup(self) -> None:
        t, cfg = self.target, self.config
        bases = {c: compute_baseline(t, c, cfg.march, cfg.mcpu) for c in ("clang", "gcc")}
        self.ref_code = assemble(bases["clang"].asm)
        self.base_cycles = {c: await self._measure(assemble(b.asm)) for c, b in bases.items()}
        self.bar = min(self.base_cycles.values(), key=lambda c: c.median)
        # llvm-mca ignores branches, so its latency for branchy baselines is
        # optimistic. The estimate stage only drops clearly worse candidates;
        # the measurement decides.
        self.mca_ceiling = 1.5 * max(b.mca.latency for b in bases.values())
        events = {}
        for c, b in bases.items():
            e = b.to_event()
            e.cycles = self.base_cycles[c]
            events[c] = e
        self.emit(BaselineReady, source=t.c_source, signature=c_signature(t), **events)
        self.context = prompt.target_context(
            t,
            [
                prompt.BaselineInfo(c, b.asm, b.mca.latency, b.mca.rthroughput, self.base_cycles[c].median)
                for c, b in bases.items()
            ],
            bases["clang"].mca.report,
        )

    def _messages(self, reasoning: bool, n: int) -> list[dict]:
        best = [prompt.CandidateInfo(b.asm, b.explanation, b.cycles.median) for b in self.best]
        fails = [prompt.CandidateInfo(a, e, failure=f) for a, e, f in self.failures]
        hint = prompt.HINTS[n % len(prompt.HINTS)]
        return prompt.build_messages(self.context, prompt.search_state(best, fails, reasoning, hint))

    async def worker(self) -> None:
        while self.proposed < self.config.candidates:
            self.proposed += 1
            n = self.proposed
            tier: ModelTier = "volume"
            if self.stall >= self.config.stall_after and not self.reasoning_in_flight:
                tier = "reasoning"
                self.emit(SearchStalled, rounds_without_improvement=self.stall)
                self.stall = 0
                self.reasoning_in_flight = True
            try:
                await self.one(n, tier)
            finally:
                if tier == "reasoning":
                    self.reasoning_in_flight = False

    async def one(self, n: int, tier: ModelTier) -> None:
        cfg = self.config
        model = self.reasoning if tier == "reasoning" else self.volume
        effort = cfg.reasoning_effort if tier == "reasoning" else cfg.volume_effort
        parent = self.best[0].candidate_id if self.best else None
        t_start = time.perf_counter()
        try:
            comp = await model.complete(self._messages(tier == "reasoning", n), effort=effort)
        except ProviderError as e:
            print(f"[{self.target.name}] model call failed: {e}")
            return
        cid = f"{self.target.name}-{n:03d}"
        harness_ms = 0.0

        def reject(stage: RejectStage, reason: str, asm: str, expl: str, ce=None) -> None:
            self.emit(
                CandidateRejected,
                candidate_id=cid,
                stage=stage,
                reason=reason,
                counterexample=ce.args if ce else None,
            )
            if stage not in ("duplicate", "benchmark"):
                self.failures.append((asm, expl, _describe_failure(stage, reason, ce)))
            self.stall += 1

        try:
            expl, asm = prompt.parse_reply(comp.text, self.target.function)
        except prompt.ParseError as e:
            expl, asm = "", comp.text[-2000:]
            self._propose(cid, parent, tier, comp, asm, expl)
            reason = "ran out of output tokens while reasoning" if comp.truncated else str(e)
            self.emit(CandidateRejected, candidate_id=cid, stage="parse", reason=reason)
            self.stall += 1
            return
        self._propose(cid, parent, tier, comp, asm, expl)

        key = _normalize(asm)
        if key in self.seen:
            reject("duplicate", "same code as an earlier candidate", asm, expl)
            return
        self.seen.add(key)

        t0 = time.perf_counter()
        try:
            code = await asyncio.to_thread(assemble, asm)
        except ToolError as e:
            reject("assemble", str(e).splitlines()[-1].split("Error: ")[-1], asm, expl)
            return
        if bad := forbidden_instructions(code) or isa_violations(code, cfg.march):
            reject("isa", "not allowed: " + ", ".join(bad), asm, expl)
            return
        res = await asyncio.to_thread(harness.difftest, self.target, self.ref_code, code)
        harness_ms += (time.perf_counter() - t0) * 1000
        if not res.passed:
            reject("test", res.reason, asm, expl, res.counterexample)
            return
        self.emit(CandidateVerified, candidate_id=cid, status="tested_only")

        est = await asyncio.to_thread(mca, asm, self.target, cfg.mcpu)
        if est.latency > self.mca_ceiling:
            reject(
                "estimate",
                f"llvm-mca latency {est.latency}, far above -O3 (ceiling {self.mca_ceiling})",
                asm,
                expl,
            )
            return

        t0 = time.perf_counter()
        cyc = await self._measure(code)
        harness_ms += (time.perf_counter() - t0) * 1000
        self.emit(
            CandidateBenchmarked,
            candidate_id=cid,
            cycles=cyc,
            mca_latency=est.latency,
            mca_rthroughput=est.rthroughput,
            speedup_vs_clang=round(self.base_cycles["clang"].median / cyc.median, 3),
            speedup_vs_gcc=round(self.base_cycles["gcc"].median / cyc.median, 3),
        )
        self.emit(
            TimingBreakdown,
            model_ms=comp.model_ms,
            harness_ms=harness_ms,
            verifier_ms=0.0,
            orchestrator_ms=max(0.0, (time.perf_counter() - t_start) * 1000 - comp.model_ms - harness_ms),
        )
        if not _faster(cyc, self.bar, cfg.min_gain):
            reject(
                "benchmark",
                f"{cyc.median:.2f} ticks/call, not faster than -O3 ({self.bar.median:.2f})",
                asm,
                expl,
            )
            return
        entry = _Best(cid, asm, expl, cyc)
        # The incumbent stays best unless beaten by more than the noise; other
        # candidates only join the population shown to the model.
        if not self.best or _faster(cyc, self.best[0].cycles, 0.0):
            self.best.insert(0, entry)
            self.emit(NewBest, candidate_id=cid)
            self.stall = 0
        else:
            self.best[1:] = sorted([*self.best[1:], entry], key=lambda b: b.cycles.median)
            self.stall += 1
        del self.best[cfg.population :]

    def _propose(self, cid, parent, tier, comp: Completion, asm: str, expl: str) -> None:
        self.emit(
            CandidateProposed,
            candidate_id=cid,
            parent_id=parent,
            model_tier=tier,
            model=comp.model,
            asm=asm,
            explanation=expl,
            model_ms=round(comp.model_ms, 1),
            tokens_in=comp.tokens_in,
            tokens_out=comp.tokens_out,
        )

    def summary(self) -> FunctionSummary:
        b = self.best[0] if self.best else None
        return FunctionSummary(
            best_candidate_id=b.candidate_id if b else None,
            status="tested_only" if b else None,
            speedup_vs_clang=round(self.base_cycles["clang"].median / b.cycles.median, 3) if b else None,
            speedup_vs_gcc=round(self.base_cycles["gcc"].median / b.cycles.median, 3) if b else None,
            candidates=self.proposed,
        )


async def run_search(
    targets: list[Target],
    volume: Model,
    reasoning: Model,
    config: SearchConfig,
    run_id: str | None = None,
    on_event=None,
    runs_dir: Path = RUNS_DIR,
) -> str:
    run_id = run_id or f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    bench_lock = asyncio.Lock()
    summary: dict[str, FunctionSummary] = {}
    with EventLog(run_id, runs_dir) as log:
        if on_event:
            log.listeners.append(on_event)
        log.emit(
            RunStarted(
                run_id=run_id,
                functions=[t.name for t in targets],
                models={"volume": volume.model, "reasoning": reasoning.model},
                config={k: v for k, v in vars(config).items()},
            )
        )
        for t in targets:
            fs = FunctionSearch(t, volume, reasoning, config, log, bench_lock)
            fs.failures = deque(maxlen=config.failures_shown)
            await fs.setup()
            async with asyncio.TaskGroup() as tg:
                for _ in range(config.concurrency):
                    tg.create_task(fs.worker())
            summary[t.name] = fs.summary()
        log.emit(RunFinished(run_id=run_id, summary=summary))
    return run_id
