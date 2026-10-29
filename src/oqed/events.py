"""Run event schema. `runs/<run_id>.jsonl` holds one event per line and is the
source of truth for the UI and for replay."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

ModelTier = Literal["volume", "reasoning"]
RejectStage = Literal[
    "parse", "duplicate", "assemble", "isa", "abi", "test", "verify", "estimate", "inline", "benchmark"
]
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
    # Judged by the project's benchmark: its unit (`ns/op`); None for the harness's TSC ticks.
    unit: str | None = None
    # Verified wins on similar functions shown in its prompt (the trick library):
    # id, function, project, explanation, status, speedup, similarity.
    tricks: list[dict] = []


class CandidateProposed(_Event):
    type: Literal["candidate_proposed"] = "candidate_proposed"
    function_id: str
    candidate_id: str
    parent_id: str | None
    model_tier: ModelTier
    model: str
    asm: str
    explanation: str
    model_ms: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None  # list-price estimate; None when the price is unknown


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
    reason: str | None = None  # why it is only tested, when the verifier could not decide


class CandidateBenchmarked(_Event):
    type: Literal["candidate_benchmarked"] = "candidate_benchmarked"
    function_id: str
    candidate_id: str
    cycles: Cycles  # latency per call, TSC ticks (or the project's benchmark, in its unit)
    mca_latency: float | None = None
    mca_rthroughput: float | None = None
    speedup_vs_clang: float
    speedup_vs_gcc: float
    speedup_vs_reference: float | None = None
    # Project benchmark: the C build timed alternately with the candidate (the
    # speedups are against it; the whole program drifts during a run).
    baseline: Cycles | None = None


class NewBest(_Event):
    type: Literal["new_best"] = "new_best"
    function_id: str
    candidate_id: str


class SearchStalled(_Event):
    type: Literal["search_stalled"] = "search_stalled"
    function_id: str
    rounds_without_improvement: int


class SearchStopped(_Event):
    """The search for this function stopped before its candidate budget:
    it converged, or the run hit its spending cap."""

    type: Literal["search_stopped"] = "search_stopped"
    function_id: str
    reason: str
    candidates: int  # proposed when it stopped


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
    cost_usd: float | None = None


class RunFinished(_Event):
    type: Literal["run_finished"] = "run_finished"
    summary: dict[str, FunctionSummary]
    cost_usd: float | None = None  # model spend for the whole run (list-price estimate)


class ScannedFunction(BaseModel):
    name: str  # target name (the function, qualified by file stem on a clash)
    function: str
    location: str  # file:line relative to the project root
    time: float  # share of the workload's samples in its code, inlined callees included
    own: float | None = None  # its own code only; these don't overlap between functions
    skipped: str | None = None  # why oqed can't search it, if it can't
    searched: bool = False  # chosen for this run


class ProjectScanned(_Event):
    """A whole-project run: every function the scan found, searched or not.
    The shares don't add up to 1; the rest is outside the project (libc, ...)."""

    type: Literal["project_scanned"] = "project_scanned"
    project: str
    functions: list[ScannedFunction]
    # Lock entries from earlier runs: function, status, speedup, run, candidate.
    locked: list[dict] = []


class ProjectMeasured(_Event):
    """`oqed report` after the run: the whole program, plain build vs oqed-cc."""

    type: Literal["project_measured"] = "project_measured"
    report: dict


Event = Annotated[
    RunStarted
    | BaselineReady
    | CandidateProposed
    | CandidateRejected
    | CandidateVerified
    | CandidateBenchmarked
    | NewBest
    | SearchStalled
    | SearchStopped
    | TimingBreakdown
    | RunFinished
    | ProjectScanned
    | ProjectMeasured,
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
        self.listeners: list[Callable[[_Event], None]] = []

    def emit(self, event: _Event) -> None:
        self._f.write(event.model_dump_json() + "\n")
        self._f.flush()
        for listener in self.listeners:
            listener(event)

    def close(self) -> None:
        self._f.close()

    def __enter__(self) -> EventLog:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def append_event(path: Path, event: _Event) -> None:
    """Add an event to a finished run's log."""
    with path.open("a") as f:
        f.write(event.model_dump_json() + "\n")


def read_events(path: Path) -> list[Event]:
    return [_adapter.validate_json(line) for line in path.read_text().splitlines() if line.strip()]
