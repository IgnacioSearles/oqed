import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { fmtTokens, fmtUsd, modelLabel, proofSkipped } from "../model";
import { Panel } from "./Panel";
const STAGE = {
    parse: "unparseable",
    duplicate: "duplicate",
    assemble: "assembler",
    isa: "ISA",
    test: "test",
    estimate: "llvm-mca",
    inline: "not inlinable",
    benchmark: "not faster",
    verify: "proof",
};
function outcome(c) {
    if (c.rejected) {
        const ce = c.rejected.counterexample;
        const args = ce ? Object.entries(ce).map(([k, v]) => `${k}=${fmt(v)}`) : [];
        return (_jsxs("span", { className: "text-ink-3", children: [_jsxs("span", { className: "text-bad", children: ["\u2717 ", STAGE[c.rejected.stage] ?? c.rejected.stage] }), " ", c.rejected.stage === "benchmark" ? "" : c.rejected.reason, args.length > 0 && _jsxs("span", { className: "text-ink-2", children: [" \u00B7 counterexample ", args.join(", ")] }), c.rejected.stage === "benchmark" && c.cycles && _jsxs("span", { children: [" ", c.cycles.median.toFixed(2), " ticks"] })] }));
    }
    if (c.status === "proven") {
        return (_jsxs("span", { className: "text-good", children: ["\u2713 proven ", _jsxs("span", { className: "text-ink-2", children: [c.cycles?.median.toFixed(2), " ticks"] })] }));
    }
    if (c.status === "tested_only" && proofSkipped(c)) {
        return (_jsxs("span", { className: "text-ink-3", title: c.statusReason ?? undefined, children: ["tested, slower than best ", _jsxs("span", { className: "text-ink-2", children: [c.cycles?.median.toFixed(2), " ticks"] })] }));
    }
    if (c.status === "tested_only") {
        return (_jsxs("span", { className: "text-warn", title: c.statusReason ?? undefined, children: ["~ tested ", _jsxs("span", { className: "text-ink-2", children: [c.cycles?.median.toFixed(2), " ticks"] })] }));
    }
    return _jsx("span", { className: "text-ink-3", children: "\u2026 in flight" });
}
/** Large values read better in hex (0x80000000 rather than 2147483648). */
function fmt(v) {
    return Math.abs(v) >= 0x10000 ? `0x${(v >>> 0).toString(16)}` : String(v);
}
/** Newest first. Stall events appear as separator rows where the search
 * handed over to the reasoning tier. */
export function CandidateFeed({ fn }) {
    const rows = [];
    let s = 0;
    for (const c of fn.candidates) {
        while (s < fn.stalls.length && fn.stalls[s].afterN < c.n) {
            rows.push({ kind: "stall", rounds: fn.stalls[s].rounds, key: `stall-${s}` });
            s++;
        }
        rows.push({ kind: "cand", c });
    }
    for (; s < fn.stalls.length; s++)
        rows.push({ kind: "stall", rounds: fn.stalls[s].rounds, key: `stall-${s}` });
    rows.reverse();
    const done = fn.stopped;
    return (_jsx(Panel, { title: "Candidates", right: "newest first", bodyClassName: "max-h-[34rem] overflow-y-auto", children: _jsxs("ol", { children: [done && (_jsxs("li", { className: "border-b border-rule-soft bg-panel-head px-2.5 py-1 text-ink-2", children: ["\u25A0 search stopped early: ", done.reason] })), rows.map((r) => r.kind === "stall" ? (_jsxs("li", { className: "border-b border-rule-soft bg-panel-head px-2.5 py-1 text-ink-2", children: ["\u25C7 stalled for ", r.rounds, " without improvement \u00B7 calling the reasoning tier"] }, r.key)) : (_jsxs("li", { className: `grid grid-cols-[2.5rem_minmax(0,1fr)] gap-x-2 border-b border-rule-soft px-2.5 py-1.5 last:border-b-0 ${fn.best?.id === r.c.id ? "bg-accent-bg" : ""}`, children: [_jsxs("span", { className: "tabular text-ink-3", children: ["#", r.c.n] }), _jsxs("span", { className: "min-w-0 space-y-0.5", children: [_jsxs("span", { className: "block text-ink-3", children: [r.c.tier === "reasoning" ? "◇ " : "", modelLabel(r.c.model), r.c.tokensOut != null && (_jsxs("span", { children: [" ", "\u00B7 ", fmtTokens((r.c.tokensIn ?? 0) + r.c.tokensOut), " tokens", r.c.cost != null && ` · ${fmtUsd(r.c.cost)}`] })), fn.best?.id === r.c.id && _jsx("span", { className: "font-bold text-accent", children: " \u00B7 best" })] }), _jsx("span", { className: "block text-ink", children: r.c.explanation || _jsx("span", { className: "text-ink-3", children: "(no explanation)" }) }), _jsx("span", { className: "block break-words", children: outcome(r.c) })] })] }, r.c.id)))] }) }));
}
