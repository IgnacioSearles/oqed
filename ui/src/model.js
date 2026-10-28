// Folds a run's event stream into per-function views for the UI.
export function buildRun(events) {
    const run = {
        id: events[0]?.run_id ?? "",
        started: events[0]?.timestamp ?? 0,
        models: {},
        config: {},
        fake: false,
        finished: false,
        functions: [],
    };
    const fns = new Map();
    const cands = new Map();
    const propose = (e, fn) => {
        const c = {
            id: e.candidate_id,
            n: fn.candidates.length + 1,
            t: e.timestamp,
            tier: e.model_tier,
            model: e.model,
            asm: e.asm,
            explanation: e.explanation,
            parentId: e.parent_id,
            tokensIn: e.tokens_in ?? null,
            tokensOut: e.tokens_out ?? null,
            cost: e.cost_usd ?? estimateCost(e.model, e.tokens_in, e.tokens_out),
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
                const fn = {
                    id: e.function_id,
                    signature: e.signature,
                    source: e.source,
                    clang: e.clang,
                    gcc: e.gcc,
                    reference: e.reference,
                    candidates: [],
                    best: null,
                    bestHistory: [],
                    stalls: [],
                    stopped: null,
                    inlined: run.config.bench === "inline",
                    timing: { model: 0, harness: 0, verifier: 0, orchestrator: 0 },
                };
                fns.set(fn.id, fn);
                run.functions.push(fn);
                break;
            }
            case "candidate_proposed": {
                const fn = fns.get(e.function_id);
                if (fn)
                    propose(e, fn);
                break;
            }
            case "candidate_rejected": {
                const c = cands.get(e.candidate_id);
                if (c)
                    c.rejected = { stage: e.stage, reason: e.reason, counterexample: e.counterexample };
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
                if (fn && c) {
                    fn.best = c;
                    if (c.cycles)
                        fn.bestHistory.push({ n: c.n, t: e.timestamp, latency: c.cycles.median });
                }
                break;
            }
            case "search_stalled": {
                const fn = fns.get(e.function_id);
                if (fn)
                    fn.stalls.push({ t: e.timestamp, afterN: fn.candidates.length, rounds: e.rounds_without_improvement });
                break;
            }
            case "search_stopped": {
                const fn = fns.get(e.function_id);
                if (fn)
                    fn.stopped = { t: e.timestamp, afterN: e.candidates, reason: e.reason };
                break;
            }
            case "timing_breakdown": {
                const fn = e.function_id ? fns.get(e.function_id) : undefined;
                if (fn) {
                    fn.timing.model += e.model_ms;
                    fn.timing.harness += e.harness_ms;
                    fn.timing.verifier += e.verifier_ms;
                    fn.timing.orchestrator += e.orchestrator_ms;
                }
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
export function bestCompiler(fn) {
    const c = fn.clang.cycles?.median ?? Infinity;
    const g = fn.gcc.cycles?.median ?? Infinity;
    return c <= g ? { name: "clang", baseline: fn.clang } : { name: "gcc", baseline: fn.gcc };
}
/** Speedup of the best candidate over the faster compiler, or null. */
export function headlineSpeedup(fn) {
    const bar = bestCompiler(fn).baseline.cycles?.median;
    const best = fn.best?.cycles?.median;
    return bar && best ? bar / best : null;
}
export function modelLabel(model) {
    const names = {
        "muse-spark-1.3": "Muse Spark",
        "muse-spark-1.3-contributor": "Muse Spark",
        "muse-wonder-1.0": "Muse Wonder",
        "meta-models/muse-spark-open-1026": "Muse Spark Open",
    };
    return names[model] ?? model;
}
export function instructionLines(asm) {
    return asm
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean);
}
/** Latency unit for axis and table labels. */
export function latencyUnit(fn) {
    return fn.inlined ? "TSC ticks/iteration, inlined" : "TSC ticks/call";
}
/** Pipeline stages in the order the search runs them, with the rejection
 * stage that filters candidates out at each step. */
export const FUNNEL = [
    { stage: "parse", label: "parsed reply" },
    { stage: "duplicate", label: "new code" },
    { stage: "assemble", label: "assembles" },
    { stage: "isa", label: "allowed ISA" },
    { stage: "test", label: "passes tests" },
    { stage: "estimate", label: "llvm-mca sane" },
    { stage: "inline", label: "inlinable" },
    { stage: "benchmark", label: "beats -O3" },
    { stage: "verify", label: "no counterexample" },
];
/** Candidates that got through each stage, and how many died there. */
export function funnel(fn) {
    const order = new Map(FUNNEL.map((f, i) => [f.stage, i]));
    const failedAt = new Array(FUNNEL.length).fill(0);
    let finished = 0;
    for (const c of fn.candidates) {
        if (c.rejected)
            failedAt[order.get(c.rejected.stage)] += 1;
        else if (c.status)
            finished += 1;
    }
    let alive = fn.candidates.length;
    const rows = FUNNEL.map((f, i) => {
        alive -= failedAt[i];
        return { label: f.label, passed: alive, failed: failedAt[i] };
    });
    // Candidates still in flight have not failed anywhere yet; only count those
    // with a final verdict as having passed the last stage.
    rows[rows.length - 1].passed = finished;
    return {
        proposed: fn.candidates.length,
        rows,
        proven: fn.candidates.filter((c) => c.status === "proven").length,
        tested: fn.candidates.filter((c) => c.status === "tested_only").length,
    };
}
/** The search skips the proof for a candidate that is not faster than a proven
 * best: it could not ship, so it stays tested only. */
export const proofSkipped = (c) => c.statusReason?.startsWith("proof skipped") ?? false;
/** USD per million tokens (input, output), mirroring oqed.providers. List
 * prices without the cached-input discount, so estimates err high. */
const PRICES = {
    "muse-spark-1.3": [1.25, 4.25],
    "muse-spark-1.3-contributor": [0.1, 0.2],
    "muse-wonder-1.0": [4, 20],
};
/** Cost for runs logged before cost_usd was recorded; null when unknown. */
function estimateCost(model, tin, tout) {
    const p = PRICES[model];
    if (!p || tin == null || tout == null)
        return null;
    return (tin * p[0] + tout * p[1]) / 1e6;
}
/** Model calls, tokens and spend per model, in the order models first appear. */
export function usage(candidates) {
    const by = new Map();
    for (const c of candidates) {
        let r = by.get(c.model);
        if (!r) {
            r = { model: c.model, tier: c.tier, calls: 0, tokensIn: 0, tokensOut: 0, cost: 0 };
            by.set(c.model, r);
        }
        r.calls += 1;
        r.tokensIn += c.tokensIn ?? 0;
        r.tokensOut += c.tokensOut ?? 0;
        r.cost = r.cost == null || c.cost == null ? null : r.cost + c.cost;
    }
    const rows = [...by.values()];
    const priced = rows.filter((r) => r.cost != null);
    const total = {
        model: "total",
        tier: "volume",
        calls: rows.reduce((a, r) => a + r.calls, 0),
        tokensIn: rows.reduce((a, r) => a + r.tokensIn, 0),
        tokensOut: rows.reduce((a, r) => a + r.tokensOut, 0),
        cost: priced.length ? priced.reduce((a, r) => a + r.cost, 0) : null,
    };
    return { rows, total };
}
export function fmtTokens(n) {
    return n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n);
}
export function fmtUsd(v) {
    if (v == null)
        return "—";
    return v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`;
}
