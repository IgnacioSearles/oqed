// Mirrors src/oqed/events.py. runs/<run_id>.jsonl holds one event per line.

export type ModelTier = "volume" | "reasoning";
export type RejectStage =
  | "parse"
  | "duplicate"
  | "assemble"
  | "isa"
  | "test"
  | "verify"
  | "estimate"
  | "inline"
  | "benchmark";
export type ProofStatus = "proven" | "tested_only";

export interface Cycles {
  median: number;
  spread: number; // interquartile range
}

export interface Baseline {
  asm: string;
  mca_rthroughput: number;
  mca_latency: number;
  cycles: Cycles | null;
}

interface Base {
  run_id: string;
  timestamp: number;
}

export interface RunStarted extends Base {
  type: "run_started";
  functions: string[];
  models: Partial<Record<ModelTier, string>>;
  config: Record<string, unknown>;
}

export interface BaselineReady extends Base {
  type: "baseline_ready";
  function_id: string;
  source: string;
  signature: string;
  clang: Baseline;
  gcc: Baseline;
  reference: Baseline | null;
}

export interface CandidateProposed extends Base {
  type: "candidate_proposed";
  function_id: string;
  candidate_id: string;
  parent_id: string | null;
  model_tier: ModelTier;
  model: string;
  asm: string;
  explanation: string;
  model_ms?: number | null;
  tokens_in?: number | null;
  tokens_out?: number | null;
  cost_usd?: number | null; // list-price estimate, logged since the spending cap was added
}

export interface CandidateRejected extends Base {
  type: "candidate_rejected";
  function_id: string;
  candidate_id: string;
  stage: RejectStage;
  reason: string;
  counterexample: Record<string, number> | null;
}

export interface CandidateVerified extends Base {
  type: "candidate_verified";
  function_id: string;
  candidate_id: string;
  status: ProofStatus;
  reason?: string | null;
}

export interface CandidateBenchmarked extends Base {
  type: "candidate_benchmarked";
  function_id: string;
  candidate_id: string;
  cycles: Cycles;
  mca_latency?: number | null;
  mca_rthroughput?: number | null;
  speedup_vs_clang: number;
  speedup_vs_gcc: number;
  speedup_vs_reference?: number | null;
}

export interface NewBest extends Base {
  type: "new_best";
  function_id: string;
  candidate_id: string;
}

export interface SearchStalled extends Base {
  type: "search_stalled";
  function_id: string;
  rounds_without_improvement: number;
}

export interface SearchStopped extends Base {
  type: "search_stopped";
  function_id: string;
  reason: string;
  candidates: number;
}

export interface TimingBreakdown extends Base {
  type: "timing_breakdown";
  function_id?: string | null;
  model_ms: number;
  harness_ms: number;
  verifier_ms: number;
  orchestrator_ms: number;
}

export interface FunctionSummary {
  best_candidate_id: string | null;
  status: ProofStatus | null;
  speedup_vs_clang: number | null;
  speedup_vs_gcc: number | null;
  candidates: number;
  cost_usd?: number | null;
}

export interface RunFinished extends Base {
  type: "run_finished";
  summary: Record<string, FunctionSummary>;
  cost_usd?: number | null;
}

export type RunEvent =
  | RunStarted
  | BaselineReady
  | CandidateProposed
  | CandidateRejected
  | CandidateVerified
  | CandidateBenchmarked
  | NewBest
  | SearchStalled
  | SearchStopped
  | TimingBreakdown
  | RunFinished;

export interface RunIndexEntry {
  id: string;
  file: string;
  demo: boolean;
  fake: boolean;
  started: number;
  functions: string[];
}

export function parseJsonl(text: string): RunEvent[] {
  return text
    .split("\n")
    .filter((l) => l.trim())
    .map((l) => JSON.parse(l) as RunEvent);
}
