"""The search loop: ask models for candidates, push each through the filter
pipeline (parse, assemble, ISA, differential test, llvm-mca estimate,
measured latency), and feed the best results and failures back into the
next prompts. When the search stalls, the reasoning tier is called.

Latency is measured inlined by default (`oqed.inline`): the candidate as an
asm statement in a loop where each result feeds the next call, against the C
compiled into the same loop by clang and gcc, in the same run. `bench="call"`
times through call/ret instead (adds ~4.7 ticks per call and can rank
candidates wrongly).

Candidates that beat -O3 are then checked by the SMT verifier: `proven` only
when it decides equivalence, `tested_only` when it can't decide, and rejected
with a counterexample when it finds one.
"""

from __future__ import annotations

import asyncio
import random
import shutil
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from . import harness, inline, prompt
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
    ProofStatus,
    RejectStage,
    RunFinished,
    RunStarted,
    SearchStalled,
    SearchStopped,
    TimingBreakdown,
)
from .isa import forbidden_instructions, isa_violations
from .providers import Completion, ProviderError
from .targets import Target, c_signature
from .toolchain import DEFAULT_MARCH, DEFAULT_MCPU, Compiler, ToolError, assemble, mca
from .verify import Verifier


class Model(Protocol):
    model: str

    async def complete(
        self, messages: list[dict], effort: str | None = None, max_tokens: int = ...
    ) -> Completion: ...


@dataclass
class SearchConfig:
    candidates: int = 24  # model calls per function, at most
    concurrency: int = 8
    stall_after: int = 8  # evaluated candidates without improvement before calling the reasoning tier
    reasoning_attempts: int = 2  # fruitless reasoning-tier calls before the function counts as converged
    volume_effort: str = "low"
    reasoning_effort: str = "high"
    population: int = 3  # candidates that beat -O3, kept as parents
    near_misses: int = 3  # correct candidates that did not beat -O3, parents until something does
    failures_shown: int = 6  # recent failures (with code) shown to the volume tier
    reasoning_failures_shown: int = 12
    tried_shown: int = 24  # one-line summaries of earlier ideas
    min_gain: float = 0.02  # a candidate must be at least 2% faster than the bar it has to beat
    verifier_workers: int = 4
    seed: int = 0  # parent selection
    max_usd: float | None = None  # stop the run once model spend reaches this
    bench: Literal["inline", "call"] = "inline"  # how latency is measured (see module docstring)
    march: str = DEFAULT_MARCH
    mcpu: str = DEFAULT_MCPU


def _faster(c: Cycles, bar: Cycles, min_gain: float) -> bool:
    """Faster by a margin that clears both the noise and `min_gain`."""
    return c.median < bar.median * (1 - min_gain) and c.median + c.spread / 2 < bar.median - bar.spread / 2


def _normalize(asm: str) -> str:
    return "\n".join(" ".join(ln.lower().split()) for ln in asm.splitlines() if ln.strip())


def _args(ce: dict[str, int]) -> str:
    return ", ".join(f"{k}={v}" for k, v in ce.items())


def _describe_failure(stage: str, reason: str, ce: harness.Counterexample | None) -> str:
    text = f"{stage}: {reason}"
    if ce:
        text += f" for {_args(ce.args)} (expected {ce.expected}, got {ce.got})"
        if ce.needs_upper_bits:
            text += "; it only fails when the undefined upper 32 bits of an argument are nonzero"
    return text


# Outcome wording for the "ideas already tried" list.
_TRIED = {
    "assemble": "did not assemble",
    "isa": "used a disallowed instruction",
    "estimate": "far slower by llvm-mca",
    "inline": "not inlinable",
}

COMPILERS: tuple[Compiler, ...] = ("clang", "gcc")
_UNIT = {"inline": "ticks/iteration", "call": "ticks/call"}
LATENCY_NOTE = {
    "inline": (
        "Latency is measured with the function inlined as an asm block into a loop where each "
        "result feeds the next call's first argument (TSC ticks per iteration, one xor of loop "
        "overhead included), against the C compiled into the same loop. Call and ret cost nothing "
        "here: what counts is the dependency chain from the arguments to the result. Code that "
        "touches memory or the stack cannot be inlined and is rejected."
    ),
    "call": "Latency is measured through call/ret (TSC ticks per call, call overhead included).",
}


@dataclass
class _Member:
    candidate_id: str
    asm: str
    explanation: str
    cycles: Cycles
    status: ProofStatus | None = None  # None for near misses
    report: str | None = None  # llvm-mca bottleneck analysis

    def info(self) -> prompt.CandidateInfo:
        return prompt.CandidateInfo(self.asm, self.explanation, self.cycles.median, status=self.status)


@dataclass
class Budget:
    """Model spend across the whole run. A call starts only when its worst-case
    cost (a reply using all of max_tokens) fits under the cap next to the
    worst cases of the calls in flight, so the cap is never exceeded."""

    limit: float | None = None
    spent: float = 0.0
    reserved: float = 0.0  # worst-case cost of the calls in flight
    unpriced: int = 0  # calls to models with no known price; the cap can't count them

    def reserve(self, worst: float | None) -> bool:
        """Claim a call's worst-case cost; False when it might not fit."""
        w = worst or 0.0
        if self.limit is not None and self.spent + self.reserved + w > self.limit:
            return False
        self.reserved += w
        return True

    def add(self, cost: float | None, reserved: float | None = None) -> None:
        self.reserved -= reserved or 0.0
        if cost is None:
            self.unpriced += 1
        else:
            self.spent += cost

    @property
    def exhausted(self) -> bool:
        return self.limit is not None and self.spent >= self.limit


@dataclass
class _Clock:
    harness_ms: float = 0.0
    verifier_ms: float = 0.0


@dataclass
class FunctionSearch:
    target: Target
    volume: Model
    reasoning: Model
    config: SearchConfig
    log: EventLog
    bench_lock: asyncio.Lock
    verifier: Verifier
    best: list[_Member] = field(default_factory=list)  # beat -O3; best[0] is the incumbent
    near: list[_Member] = field(default_factory=list)  # correct, measured, not faster than -O3
    seen: set[str] = field(default_factory=set)
    seen_code: set[bytes] = field(default_factory=set)
    stall: int = 0
    proposed: int = 0
    reasoning_in_flight: bool = False
    reasoning_since_best: int = 0
    reasoning_unaffordable: bool = False  # a reasoning-tier reply no longer fits under the cap
    done: bool = False
    budget: Budget = field(default_factory=Budget)
    spent: float = 0.0  # this function's share

    def __post_init__(self) -> None:
        cfg = self.config
        self.failures: deque = deque(maxlen=max(cfg.failures_shown, cfg.reasoning_failures_shown))
        self.tried: deque[str] = deque(maxlen=cfg.tried_shown)
        self.rng = random.Random(f"{cfg.seed}:{self.target.name}")

    def emit(self, event_cls, **kw) -> None:
        self.log.emit(event_cls(run_id=self.log.run_id, function_id=self.target.name, **kw))

    async def _measure(self, code: bytes) -> Cycles:
        async with self.bench_lock:
            return await asyncio.to_thread(harness.measure, code, self.target, True)

    async def _measure_inline(self, asm: str) -> dict[Compiler, inline.InlineResult]:
        """Build with both compilers in parallel, then time one at a time."""
        t, march = self.target, self.config.march
        built = await asyncio.gather(
            *(asyncio.to_thread(inline.build, asm, t, c, march) for c in COMPILERS), return_exceptions=True
        )
        if errs := [b for b in built if isinstance(b, BaseException)]:
            for b in built:
                if isinstance(b, inline.Built):
                    shutil.rmtree(b.dir, ignore_errors=True)
            raise errs[0]
        async with self.bench_lock:
            return {b.compiler: await asyncio.to_thread(inline.run, b) for b in built}

    async def _latency(self, asm: str, code: bytes) -> tuple[Cycles, dict[Compiler, Cycles]]:
        """The candidate's latency and the C's per compiler. Inlined, the C is
        timed in the same run, so both see the same machine state."""
        if self.config.bench == "call":
            return await self._measure(code), self.base_cycles
        res = await self._measure_inline(asm)
        cand = min((r.dep_cand for r in res.values()), key=lambda c: c.median)
        return cand, {c: r.dep_ref for c, r in res.items()}

    async def setup(self) -> None:
        t, cfg = self.target, self.config
        bases = {c: compute_baseline(t, c, cfg.march, cfg.mcpu) for c in ("clang", "gcc")}
        self.ref_code = assemble(bases["clang"].asm)
        self.unit = _UNIT[cfg.bench]
        if cfg.bench == "call":
            self.base_cycles = {c: await self._measure(assemble(b.asm)) for c, b in bases.items()}
        else:
            # The program times the C on its own; the compiler's asm is just a placeholder candidate.
            res = await self._measure_inline(bases["clang"].asm)
            self.base_cycles = {c: r.dep_ref for c, r in res.items()}
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
            LATENCY_NOTE[cfg.bench],
        )

    def _pool(self) -> list[_Member]:
        """Parents, fastest first. The incumbent is proven-first (it is what
        ships), but a faster tested-only candidate is the better parent."""
        return sorted(self.best, key=lambda m: m.cycles.median) if self.best else self.near

    def _pick_parent(self) -> int | None:
        """Rank-weighted choice from the pool: the fastest most often, the
        others often enough to keep alternative lines alive."""
        pool = self._pool()
        if not pool:
            return None
        return self.rng.choices(range(len(pool)), weights=[len(pool) - i for i in range(len(pool))])[0]

    def _messages(self, reasoning: bool, n: int) -> tuple[list[dict], str | None]:
        cfg = self.config
        fails = [prompt.CandidateInfo(a, e, failure=f) for a, e, f in self.failures]
        parent = None if reasoning else self._pick_parent()
        state = prompt.SearchState(
            best=[b.info() for b in self._pool()] if self.best else [],
            near=[b.info() for b in self.near],
            failures=fails[-(cfg.reasoning_failures_shown if reasoning else cfg.failures_shown) :],
            tried=list(self.tried),
            parent=parent,
            hint=prompt.HINTS[n % len(prompt.HINTS)],
            best_report=self._pool()[0].report if self.best else None,
        )
        pool = self._pool()
        # Reasoning-tier calls see the whole population; record the fastest as their parent.
        parent_id = pool[parent or 0].candidate_id if pool else None
        return prompt.build_messages(self.context, prompt.search_state(state, reasoning)), parent_id

    async def worker(self) -> None:
        cfg = self.config
        while not self.done and self.proposed < cfg.candidates:
            tier: ModelTier = "volume"
            if self.budget.exhausted:
                self.stop(f"spending cap reached: ${self.budget.spent:.2f} of ${self.budget.limit:.2f}")
                return
            # Checked even while a reasoning call is in flight: it can take minutes, and the
            # volume tier would otherwise spend the whole budget repeating itself meanwhile.
            # The in-flight call still finishes and can still win.
            if self.stall >= cfg.stall_after and self.reasoning_since_best >= cfg.reasoning_attempts:
                self.stop(
                    f"no improvement after {self.reasoning_since_best} reasoning-tier attempts "
                    f"and {self.stall} further candidates"
                )
                return
            can_reason = not self.reasoning_in_flight and not self.reasoning_unaffordable
            if self.stall >= cfg.stall_after and can_reason:
                tier = "reasoning"
                self.emit(SearchStalled, rounds_without_improvement=self.stall)
                self.stall = 0
                self.reasoning_in_flight = True
                self.reasoning_since_best += 1
            self.proposed += 1
            started = False
            try:
                started = await self.one(self.proposed, tier)
            finally:
                if tier == "reasoning":
                    self.reasoning_in_flight = False
            if not started:
                self.proposed -= 1
                if tier == "volume":
                    spent = self.budget.spent
                    self.stop(f"spending cap reached: ${spent:.2f} spent, the next call might not fit")
                    return
                # Count the attempt so a stalled search can still converge without it.
                self.reasoning_unaffordable = True
                self.reasoning_since_best = cfg.reasoning_attempts

    def stop(self, reason: str) -> None:
        if not self.done:
            self.done = True
            self.emit(SearchStopped, reason=reason, candidates=self.proposed)

    async def one(self, n: int, tier: ModelTier) -> bool:
        """Propose and evaluate one candidate; False when the call would not fit under the cap."""
        cfg = self.config
        model = self.reasoning if tier == "reasoning" else self.volume
        effort = cfg.reasoning_effort if tier == "reasoning" else cfg.volume_effort
        messages, parent = self._messages(tier == "reasoning", n)
        worst = model.worst_cost(messages) if hasattr(model, "worst_cost") else None
        if not self.budget.reserve(worst):
            left = (self.budget.limit or 0) - self.budget.spent - self.budget.reserved
            print(
                f"[{self.target.name}] {model.model} not called: a reply could cost ${worst:.2f}, "
                f"${max(left, 0):.2f} is left under the cap"
            )
            return False
        t_start = time.perf_counter()
        try:
            comp = await model.complete(messages, effort=effort)
        except ProviderError as e:
            self.budget.add(0.0, reserved=worst)
            print(f"[{self.target.name}] model call failed: {e}")
            return True
        self.budget.add(comp.cost_usd, reserved=worst)
        self.spent += comp.cost_usd or 0.0
        clock = _Clock()
        try:
            await self._evaluate(f"{self.target.name}-{n:03d}", parent, tier, comp, clock)
        finally:
            total = (time.perf_counter() - t_start) * 1000
            self.emit(
                TimingBreakdown,
                model_ms=comp.model_ms,
                harness_ms=clock.harness_ms,
                verifier_ms=clock.verifier_ms,
                orchestrator_ms=max(0.0, total - comp.model_ms - clock.harness_ms - clock.verifier_ms),
            )
        return True

    async def _evaluate(self, cid: str, parent: str | None, tier: ModelTier, comp: Completion, clock: _Clock):
        cfg = self.config

        def reject(stage: RejectStage, reason: str, asm: str, expl: str, ce=None, tried: str | None = None):
            self.emit(
                CandidateRejected,
                candidate_id=cid,
                stage=stage,
                reason=reason,
                counterexample=ce.args if ce else None,
            )
            if stage not in ("duplicate", "benchmark"):
                self.failures.append((asm, expl, _describe_failure(stage, reason, ce)))
            if stage != "duplicate":
                self.tried.append(f"{expl or '(no explanation)'} -> {tried or _TRIED.get(stage, stage)}")
            self.stall += 1

        try:
            expl, asm = prompt.parse_reply(comp.text, self.target.function)
        except prompt.ParseError as e:
            self._propose(cid, parent, tier, comp, comp.text[-2000:], "")
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
            clock.harness_ms += (time.perf_counter() - t0) * 1000
            reject("assemble", str(e).splitlines()[-1].split("Error: ")[-1], asm, expl)
            return
        # Different spelling, same bytes (hex vs decimal immediates, aliases).
        if code in self.seen_code:
            clock.harness_ms += (time.perf_counter() - t0) * 1000
            reject("duplicate", "same machine code as an earlier candidate", asm, expl)
            return
        self.seen_code.add(code)
        if bad := forbidden_instructions(code) or isa_violations(code, cfg.march):
            clock.harness_ms += (time.perf_counter() - t0) * 1000
            reject("isa", "not allowed: " + ", ".join(bad), asm, expl)
            return
        res = await asyncio.to_thread(harness.difftest, self.target, self.ref_code, code)
        clock.harness_ms += (time.perf_counter() - t0) * 1000
        if not res.passed:
            ce = res.counterexample
            reject("test", res.reason, asm, expl, ce, tried=f"wrong for {_args(ce.args)}" if ce else "wrong")
            return

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
        try:
            cyc, refs = await self._latency(asm, code)
        except inline.NotInlinable as e:
            reject("inline", f"not inlinable: {e}", asm, expl)
            return
        except inline.Mismatch as e:
            reject("inline", str(e), asm, expl, tried="wrong once inlined")
            return
        except ToolError as e:
            reject("inline", f"does not compile as inline asm: {str(e)[:300]}", asm, expl)
            return
        finally:
            clock.harness_ms += (time.perf_counter() - t0) * 1000
        bar = min(refs.values(), key=lambda c: c.median)
        self.emit(
            CandidateBenchmarked,
            candidate_id=cid,
            cycles=cyc,
            mca_latency=est.latency,
            mca_rthroughput=est.rthroughput,
            speedup_vs_clang=round(refs["clang"].median / cyc.median, 3),
            speedup_vs_gcc=round(refs["gcc"].median / cyc.median, 3),
        )

        if not _faster(cyc, bar, cfg.min_gain):
            slower = f"{cyc.median:.2f} {self.unit}, not faster than -O3 ({bar.median:.2f})"
            reject("benchmark", slower, asm, expl, tried=f"correct but {slower}")
            self.near = sorted([*self.near, _Member(cid, asm, expl, cyc)], key=lambda m: m.cycles.median)[
                : cfg.near_misses
            ]
            return

        # Listed before the proof: proofs can take a while, and meanwhile other
        # calls should know this idea is taken.
        self.tried.append(f"{expl or '(no explanation)'} -> beats -O3 at {cyc.median:.2f} {self.unit}")
        # Proofs are the expensive stage, so only candidates that beat -O3 get one.
        v = await self.verifier.check_async(self.target, self.ref_code, code)
        clock.verifier_ms = v.elapsed_ms
        if v.status == "not_equivalent":
            self.emit(
                CandidateRejected,
                candidate_id=cid,
                stage="verify",
                reason=v.reason,
                counterexample=v.counterexample,
            )
            args = _args(v.counterexample or {})
            self.failures.append((asm, expl, f"verify: SMT proof found a counterexample: {args}: {v.reason}"))
            self.tried.append(f"{expl or '(no explanation)'} -> wrong for {args} (found by the SMT proof)")
            self.stall += 1
            return
        status: ProofStatus = "proven" if v.status == "equivalent" else "tested_only"
        self.emit(CandidateVerified, candidate_id=cid, status=status, reason=v.reason or None)

        entry = _Member(cid, asm, expl, cyc, status, est.report)
        # Proven beats tested-only. Otherwise the incumbent stays best unless
        # beaten by more than the noise; other candidates only join the
        # population.
        inc = self.best[0] if self.best else None
        if (
            inc is None
            or (status == "proven" and inc.status != "proven")
            or (status == inc.status and _faster(cyc, inc.cycles, 0.0))
        ):
            self.best.insert(0, entry)
            self.emit(NewBest, candidate_id=cid)
            self.stall = 0
            self.reasoning_since_best = 0
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
            cost_usd=round(comp.cost_usd, 5) if comp.cost_usd is not None else None,
        )

    def summary(self) -> FunctionSummary:
        b = self.best[0] if self.best else None
        return FunctionSummary(
            best_candidate_id=b.candidate_id if b else None,
            status=b.status if b else None,
            speedup_vs_clang=round(self.base_cycles["clang"].median / b.cycles.median, 3) if b else None,
            speedup_vs_gcc=round(self.base_cycles["gcc"].median / b.cycles.median, 3) if b else None,
            candidates=self.proposed,
            cost_usd=round(self.spent, 4),
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
    verifier = Verifier(workers=config.verifier_workers)
    summary: dict[str, FunctionSummary] = {}
    budget = Budget(config.max_usd)
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
            if budget.exhausted:
                print(f"[{t.name}] skipped: spending cap reached (${budget.spent:.2f})")
                continue
            fs = FunctionSearch(t, volume, reasoning, config, log, bench_lock, verifier, budget=budget)
            await fs.setup()
            async with asyncio.TaskGroup() as tg:
                for _ in range(config.concurrency):
                    tg.create_task(fs.worker())
            summary[t.name] = fs.summary()
        if budget.unpriced:
            print(f"note: {budget.unpriced} calls to models without a known price are not in the spend total")
        log.emit(RunFinished(run_id=run_id, summary=summary, cost_usd=round(budget.spent, 4)))
    verifier.close()
    return run_id
