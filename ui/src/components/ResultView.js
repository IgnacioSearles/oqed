import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMemo } from "react";
import { diffLines } from "../diff";
import { bestCompiler, fmtTokens, fmtUsd, headlineSpeedup, instructionLines, latencyUnit, modelLabel, usage, } from "../model";
import { AsmColumn } from "./AsmColumn";
import { LatencyBars } from "./LatencyBars";
import { Panel } from "./Panel";
import { StatusBadge } from "./StatusBadge";
const same = (n) => new Array(n).fill("same");
const countInsns = (lines) => lines.filter((l) => !l.endsWith(":")).length;
export function ResultView({ fn }) {
    const best = fn.best;
    const faster = bestCompiler(fn);
    const speedup = headlineSpeedup(fn);
    const bar = faster.baseline.cycles?.median;
    const spend = usage(fn.candidates).total;
    // The best is proven-first; a faster candidate that could only be tested is worth naming.
    const unproven = fn.candidates
        .filter((c) => c.status === "tested_only" && c.cycles && best?.cycles && c.cycles.median < best.cycles.median)
        .sort((a, b) => a.cycles.median - b.cycles.median)[0];
    const listings = useMemo(() => {
        const oqed = best ? instructionLines(best.asm) : [];
        const listing = (asm) => {
            const lines = instructionLines(asm);
            return { lines, marks: best ? diffLines(lines, oqed).a : same(lines.length) };
        };
        const cols = [
            { key: "clang", title: "clang -O3", ...listing(fn.clang.asm), base: fn.clang },
            { key: "gcc", title: "gcc -O3", ...listing(fn.gcc.asm), base: fn.gcc },
        ];
        if (fn.reference) {
            cols.push({ key: "ref", title: "hand-written", ...listing(fn.reference.asm), base: fn.reference });
        }
        // oqed's `+` lines are relative to the faster compiler's output.
        const oqedMarks = best ? diffLines(instructionLines(faster.baseline.asm), oqed).b : [];
        return { cols, oqed, oqedMarks };
    }, [fn, best, faster]);
    const rows = [
        ...listings.cols
            .filter((c) => c.base.cycles)
            .map((c) => ({
            key: c.key,
            label: c.title,
            cycles: c.base.cycles,
            mcaLatency: c.base.mca_latency,
            instructions: countInsns(c.lines),
            speedup: bar ? bar / c.base.cycles.median : null,
        })),
        ...(best?.cycles
            ? [
                {
                    key: "oqed",
                    label: "oqed",
                    cycles: best.cycles,
                    mcaLatency: best.mcaLatency,
                    instructions: countInsns(listings.oqed),
                    speedup,
                    emphasis: true,
                },
            ]
            : []),
    ];
    const nCols = listings.cols.length + (best ? 1 : 0);
    return (_jsxs("div", { className: "space-y-1.5", children: [_jsx(Panel, { title: fn.id, right: _jsx(StatusBadge, { status: best?.status, reason: best?.statusReason }), children: _jsxs("div", { className: "grid gap-x-6 gap-y-2 px-2.5 py-2 md:grid-cols-[auto_minmax(0,1fr)]", children: [_jsx("div", { className: "text-ink-3", children: "signature" }), _jsx("div", { className: "break-all text-ink-2", children: fn.signature }), _jsx("div", { className: "text-ink-3", children: "result" }), _jsx("div", { children: best && speedup ? (_jsxs("span", { children: [_jsxs("span", { className: "text-xl font-bold text-accent", children: [speedup.toFixed(2), "\u00D7"] }), " ", _jsxs("span", { className: "text-ink-2", children: ["lower latency than ", faster.name, " -O3 (the faster compiler)"] })] })) : (_jsxs("span", { className: "text-ink-2", children: ["no candidate beat ", faster.name, " -O3 by more than the measurement noise (", fn.candidates.length, " tried)"] })) }), best && (_jsxs(_Fragment, { children: [_jsx("div", { className: "text-ink-3", children: "trick" }), _jsx("div", { className: "text-ink", children: best.explanation || "—" }), best.status === "proven" && unproven && bar && (_jsxs(_Fragment, { children: [_jsx("div", { className: "text-ink-3", children: "unproven" }), _jsxs("div", { className: "text-ink-2", children: [_jsx("span", { className: "text-warn", children: "~" }), " candidate ", unproven.n, " reaches", " ", (bar / unproven.cycles.median).toFixed(2), "\u00D7 but is tested only", unproven.statusReason ? ` (${unproven.statusReason})` : "", ", so it does not ship"] })] })), _jsx("div", { className: "text-ink-3", children: "found by" }), _jsxs("div", { className: "text-ink-2", children: [modelLabel(best.model), " (", best.tier === "reasoning" ? "reasoning" : "volume", " tier), candidate", " ", best.n, " of ", fn.candidates.length] })] })), spend.calls > 0 && (_jsxs(_Fragment, { children: [_jsx("div", { className: "text-ink-3", children: "search cost" }), _jsxs("div", { className: "text-ink-2", children: [spend.cost != null ? `${fmtUsd(spend.cost)} · ` : "", fmtTokens(spend.tokensIn + spend.tokensOut), " tokens over ", spend.calls, " model calls", spend.cost != null && _jsx("span", { className: "text-ink-3", children: " (list-price estimate)" })] })] }))] }) }), rows.length > 0 && _jsx(LatencyBars, { rows: rows, unit: latencyUnit(fn) }), _jsx(Panel, { title: "C source", right: "reference semantics", bodyClassName: "overflow-x-auto", children: _jsx("pre", { className: "px-2.5 py-2 leading-[1.6] text-ink-2", children: fn.source.trim() }) }), _jsxs("div", { className: `grid gap-1.5 md:grid-cols-2 ${nCols >= 4 ? "xl:grid-cols-4" : nCols === 3 ? "xl:grid-cols-3" : ""}`, children: [listings.cols.map((c) => (_jsx(AsmColumn, { title: c.title, note: c.key === faster.name ? "faster compiler" : undefined, lines: c.lines, marks: c.marks }, c.key))), best && (_jsx(AsmColumn, { title: "oqed", note: modelLabel(best.model), lines: listings.oqed, marks: listings.oqedMarks, emphasis: true }))] })] }));
}
