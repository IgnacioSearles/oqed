"""Run event schema. `runs/<run_id>.jsonl` holds one event per line and is the
source of truth for the UI and for replay."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

ModelTier = Literal["volume", "reasoning"]
RejectStage = Literal["assemble", "test", "verify", "estimate", "benchmark"]
ProofStatus = Literal["proven", "tested_only"]

RUNS_DIR = Path(__file__).resolve().parents[2] / "runs"


class _Event(BaseModel):
    run_id: str
    timestamp: float = Field(default_factory=time.time)


class Cycles(BaseModel):
    median: float
    spread: float  # interquartile range


class Baseline(BaseModel):
    asm: str
    mca_rthroughput: float
    mca_latency: float
    cycles: Cycles | None = None  # filled in once the harness has measured it


class RunStarted(_Event):
    type: Literal["run_started"] = "run_started"
    functions: list[str]
    models: dict[ModelTier, str]
    config: dict


class BaselineReady(_Event):
    type: Literal["baseline_ready"] = "baseline_ready"
    function_id: str
    source: str
    signature: str
    clang: Baseline
    gcc: Baseline
    reference: Baseline | None = None


class CandidateProposed(_Event):
    type: Literal["candidate_proposed"] = "candidate_proposed"
    function_id: str
    candidate_id: str
    parent_id: str | None
    model_tier: ModelTier
    model: str
    asm: str
    explanation: str


class CandidateRejected(_Event):
    type: Literal["candidate_rejected"] = "candidate_rejected"
    function_id: str
    candidate_id: str
    stage: RejectStage
    reason: str
    counterexample: dict[str, int] | None = None  # argument name -> value


class CandidateVerified(_Event):
    type: Literal["candidate_verified"] = "candidate_verified"
    function_id: str
    candidate_id: str
    status: ProofStatus


class CandidateBenchmarked(_Event):
    type: Literal["candidate_benchmarked"] = "candidate_benchmarked"
    function_id: str
    candidate_id: str
    cycles: Cycles
    speedup_vs_clang: float
    speedup_vs_gcc: float
    speedup_vs_reference: float | None = None


class NewBest(_Event):
    type: Literal["new_best"] = "new_best"
    function_id: str
    candidate_id: str


class SearchStalled(_Event):
    type: Literal["search_stalled"] = "search_stalled"
    function_id: str
    rounds_without_improvement: int


class TimingBreakdown(_Event):
    type: Literal["timing_breakdown"] = "timing_breakdown"
    function_id: str | None = None
    model_ms: float
    harness_ms: float
    verifier_ms: float
    orchestrator_ms: float


class FunctionSummary(BaseModel):
    best_candidate_id: str | None
    status: ProofStatus | None
    speedup_vs_clang: float | None
    speedup_vs_gcc: float | None
    candidates: int


class RunFinished(_Event):
    type: Literal["run_finished"] = "run_finished"
    summary: dict[str, FunctionSummary]


Event = Annotated[
    RunStarted
    | BaselineReady
    | CandidateProposed
    | CandidateRejected
    | CandidateVerified
    | CandidateBenchmarked
    | NewBest
    | SearchStalled
    | TimingBreakdown
    | RunFinished,
    Field(discriminator="type"),
]

_adapter: TypeAdapter[Event] = TypeAdapter(Event)


class EventLog:
    """Append-only JSONL writer for one run."""

    def __init__(self, run_id: str, runs_dir: Path = RUNS_DIR):
        runs_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.path = runs_dir / f"{run_id}.jsonl"
        self._f = self.path.open("a")

    def emit(self, event: _Event) -> None:
        self._f.write(event.model_dump_json() + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()

    def __enter__(self) -> EventLog:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def read_events(path: Path) -> list[Event]:
    return [_adapter.validate_json(line) for line in path.read_text().splitlines() if line.strip()]
