// Folds a run's event stream into per-function views for the UI.

import type {
  Baseline,
  CandidateProposed,
  Cycles,
  ModelTier,
  ProofStatus,
  RejectStage,
  RunEvent,
} from "./events";

export interface Candidate {
  id: string;
  n: number; // 1-based order of proposal
  tier: ModelTier;
  model: string;
  asm: string;
  explanation: string;
  parentId: string | null;
  rejected?: { stage: RejectStage; reason: string; counterexample: Record<string, number> | null };
  status?: ProofStatus;
  statusReason?: string | null;
  cycles?: Cycles;
  mcaLatency?: number | null;
  mcaThroughput?: number | null;
}

export interface FunctionView {
  id: string;
  signature: string;
  source: string;
  clang: Baseline;
  gcc: Baseline;
  reference: Baseline | null;
  candidates: Candidate[];
  best: Candidate | null;
  stalls: number;
}

export interface RunView {
  id: string;
  started: number;
  models: Partial<Record<ModelTier, string>>;
  config: Record<string, unknown>;
  fake: boolean;
  finished: boolean;
  functions: FunctionView[];
}

export function buildRun(events: RunEvent[]): RunView {
  const run: RunView = {
    id: events[0]?.run_id ?? "",
    started: events[0]?.timestamp ?? 0,
    models: {},
    config: {},
    fake: false,
    finished: false,
    functions: [],
  };
  const fns = new Map<string, FunctionView>();
  const cands = new Map<string, Candidate>();

  const propose = (e: CandidateProposed, fn: FunctionView) => {
    const c: Candidate = {
      id: e.candidate_id,
      n: fn.candidates.length + 1,
      tier: e.model_tier,
      model: e.model,
      asm: e.asm,
      explanation: e.explanation,
      parentId: e.parent_id,
    };
    fn.candidates.push(c);
    cands.set(c.id, c);
  };

  for (const e of events) {
    switch (e.type) {
      case "run_started":
        run.models = e.models;
        run.config = e.config;
        run.fake = Boolean(e.config.fake);
        break;
      case "baseline_ready": {
        const fn: FunctionView = {
          id: e.function_id,
          signature: e.signature,
          source: e.source,
          clang: e.clang,
          gcc: e.gcc,
          reference: e.reference,
          candidates: [],
          best: null,
          stalls: 0,
        };
        fns.set(fn.id, fn);
        run.functions.push(fn);
        break;
      }
      case "candidate_proposed": {
        const fn = fns.get(e.function_id);
        if (fn) propose(e, fn);
        break;
      }
      case "candidate_rejected": {
        const c = cands.get(e.candidate_id);
        if (c) c.rejected = { stage: e.stage, reason: e.reason, counterexample: e.counterexample };
        break;
      }
      case "candidate_verified": {
        const c = cands.get(e.candidate_id);
        if (c) {
          c.status = e.status;
          c.statusReason = e.reason;
        }
        break;
      }
      case "candidate_benchmarked": {
        const c = cands.get(e.candidate_id);
        if (c) {
          c.cycles = e.cycles;
          c.mcaLatency = e.mca_latency;
          c.mcaThroughput = e.mca_rthroughput;
        }
        break;
      }
      case "new_best": {
        const fn = fns.get(e.function_id);
        const c = cands.get(e.candidate_id);
        if (fn && c) fn.best = c;
        break;
      }
      case "search_stalled": {
        const fn = fns.get(e.function_id);
        if (fn) fn.stalls += 1;
        break;
      }
      case "run_finished":
        run.finished = true;
        break;
    }
  }
  return run;
}

/** The faster of the two compilers by measured median latency. */
export function bestCompiler(fn: FunctionView): { name: "clang" | "gcc"; baseline: Baseline } {
  const c = fn.clang.cycles?.median ?? Infinity;
  const g = fn.gcc.cycles?.median ?? Infinity;
  return c <= g ? { name: "clang", baseline: fn.clang } : { name: "gcc", baseline: fn.gcc };
}

/** Speedup of the best candidate over the faster compiler, or null. */
export function headlineSpeedup(fn: FunctionView): number | null {
  const bar = bestCompiler(fn).baseline.cycles?.median;
  const best = fn.best?.cycles?.median;
  return bar && best ? bar / best : null;
}

export function modelLabel(model: string): string {
  const names: Record<string, string> = {
    "muse-spark-1.3": "Muse Spark",
    "muse-spark-1.3-contributor": "Muse Spark",
    "muse-wonder-1.0": "Muse Wonder",
    "meta-models/muse-spark-open-1026": "Muse Spark Open",
  };
  return names[model] ?? model;
}

export function instructionLines(asm: string): string[] {
  return asm
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
}
