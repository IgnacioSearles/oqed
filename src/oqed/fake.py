"""Synthetic run generator for UI development. Baselines are real; everything
the search would produce (candidates, verdicts, cycle counts) is made up, and
the run is labelled `fake` in its id and config so it is never mistaken for a
real result."""

from __future__ import annotations

import random
import time
import uuid

from . import baseline
from .events import (
    BaselineReady,
    CandidateBenchmarked,
    CandidateProposed,
    CandidateRejected,
    CandidateVerified,
    Cycles,
    EventLog,
    FunctionSummary,
    NewBest,
    RunFinished,
    RunStarted,
    SearchStalled,
    TimingBreakdown,
)
from .targets import Target, c_signature
from .toolchain import instruction_lines

MODELS = {"volume": "muse-spark-1.3", "reasoning": "muse-wonder-1.0"}

_EXPLANATIONS = [
    "Replace the compare-and-branch with a flag-based cmov chain.",
    "Use sbb to materialize the sign mask without a shift.",
    "Fold the two clamps into one unsigned compare.",
    "Break the multiply dependency by precomputing the shifted term.",
    "Use lea to combine the add and shift into one instruction.",
    "Drop the redundant zero-extension; the 32-bit write already clears the top half.",
]
_REJECT_REASONS = {
    "assemble": "invalid operand combination for `cmovns`",
    "test": "output mismatch",
    "verify": "SMT solver found differing input",
    "estimate": "llvm-mca estimate not better than baseline",
    "benchmark": "not faster than baseline (within noise)",
}


def _mutate(asm: str, rng: random.Random) -> str:
    lines = instruction_lines(asm)
    body = lines[:-1]
    if len(body) > 1 and rng.random() < 0.6:
        del body[rng.randrange(len(body))]
    if len(body) > 1:
        i = rng.randrange(len(body) - 1)
        body[i], body[i + 1] = body[i + 1], body[i]
    return "".join(f"\t{ln}\n" for ln in body + ["ret"])


def _counterexample(target: Target, rng: random.Random) -> dict[str, int]:
    picks = [0, 1, -1, 0x7FFFFFFF, -0x80000000, 255, 256, rng.randrange(-(2**31), 2**31)]
    return {chr(ord("a") + i): rng.choice(picks) for i in range(len(target.args))}


def generate(targets: list[Target], candidates_per_fn: int = 40, seed: int = 0, delay: float = 0.0) -> str:
    rng = random.Random(seed)
    run_id = f"fake-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    summary: dict[str, FunctionSummary] = {}
    with EventLog(run_id) as log:
        log.emit(
            RunStarted(
                run_id=run_id, functions=[t.name for t in targets], models=MODELS, config={"fake": True}
            )
        )
        for t in targets:
            clang, gcc = baseline.compute(t, "clang"), baseline.compute(t, "gcc")
            cl, gc = clang.to_event(), gcc.to_event()
            cl.cycles = Cycles(median=round(clang.mca.latency + 4 + rng.random(), 2), spread=0.3)
            gc.cycles = Cycles(median=round(gcc.mca.latency + 4 + rng.random(), 2), spread=0.3)
            log.emit(
                BaselineReady(
                    run_id=run_id,
                    function_id=t.name,
                    source=t.c_source,
                    signature=c_signature(t),
                    clang=cl,
                    gcc=gc,
                )
            )
            base_cycles = best_cycles = min(cl.cycles.median, gc.cycles.median)
            best_id, best_status, stall = None, None, 0
            stall_at = candidates_per_fn // 2
            for n in range(candidates_per_fn):
                tier = "reasoning" if stall >= 8 else "volume"
                if stall == 8:
                    log.emit(
                        SearchStalled(run_id=run_id, function_id=t.name, rounds_without_improvement=stall)
                    )
                cid = f"{t.name}-{n:03d}"
                log.emit(
                    CandidateProposed(
                        run_id=run_id,
                        function_id=t.name,
                        candidate_id=cid,
                        parent_id=best_id,
                        model_tier=tier,
                        model=MODELS[tier],
                        asm=_mutate(clang.asm, rng),
                        explanation=rng.choice(_EXPLANATIONS),
                    )
                )
                stage = rng.choices(
                    ["assemble", "test", "verify", "estimate", "benchmark", None],
                    [2, 6, 2, 2, 3, 2 if tier == "volume" and n < stall_at else 6],
                )[0]
                if stage is not None:
                    log.emit(
                        CandidateRejected(
                            run_id=run_id,
                            function_id=t.name,
                            candidate_id=cid,
                            stage=stage,
                            reason=_REJECT_REASONS[stage],
                            counterexample=_counterexample(t, rng) if stage in ("test", "verify") else None,
                        )
                    )
                    stall += 1
                else:
                    status = "proven" if t.loop_free else "tested_only"
                    log.emit(
                        CandidateVerified(run_id=run_id, function_id=t.name, candidate_id=cid, status=status)
                    )
                    cyc = round(base_cycles * rng.uniform(0.4, 0.98), 2)
                    log.emit(
                        CandidateBenchmarked(
                            run_id=run_id,
                            function_id=t.name,
                            candidate_id=cid,
                            cycles=Cycles(median=cyc, spread=0.2),
                            speedup_vs_clang=round(cl.cycles.median / cyc, 3),
                            speedup_vs_gcc=round(gc.cycles.median / cyc, 3),
                        )
                    )
                    if cyc < best_cycles:
                        best_cycles, best_id, best_status, stall = cyc, cid, status, 0
                        log.emit(NewBest(run_id=run_id, function_id=t.name, candidate_id=cid))
                    else:
                        stall += 1
                log.emit(
                    TimingBreakdown(
                        run_id=run_id,
                        function_id=t.name,
                        model_ms=rng.uniform(800, 4000),
                        harness_ms=rng.uniform(5, 40),
                        verifier_ms=rng.uniform(50, 2000),
                        orchestrator_ms=rng.uniform(1, 5),
                    )
                )
                if delay:
                    time.sleep(delay)
            summary[t.name] = FunctionSummary(
                best_candidate_id=best_id,
                status=best_status,
                speedup_vs_clang=round(cl.cycles.median / best_cycles, 3) if best_id else None,
                speedup_vs_gcc=round(gc.cycles.median / best_cycles, 3) if best_id else None,
                candidates=candidates_per_fn,
            )
        log.emit(RunFinished(run_id=run_id, summary=summary))
    return run_id
