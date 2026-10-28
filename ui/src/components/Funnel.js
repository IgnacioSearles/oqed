import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { funnel } from "../model";
import { Panel } from "./Panel";
/** Candidates surviving each pipeline stage, in the order the search runs them. */
export function Funnel({ fn }) {
    const f = funnel(fn);
    const max = Math.max(1, f.proposed);
    const t = fn.timing;
    const total = t.model + t.harness + t.verifier + t.orchestrator;
    const share = (v) => {
        if (!total)
            return "—";
        const pct = (v / total) * 100;
        return v > 0 && pct < 1 ? "<1%" : `${Math.round(pct)}%`;
    };
    return (_jsxs(Panel, { title: "Funnel", right: `${f.proposed} proposed`, children: [_jsxs("table", { className: "tabular w-full border-collapse", children: [_jsx("thead", { children: _jsxs("tr", { className: "border-b border-rule-soft text-left text-ink-3", children: [_jsx("th", { className: "px-2.5 py-1 font-normal", children: "stage" }), _jsx("th", { className: "w-[45%] px-2.5 py-1 font-normal" }), _jsx("th", { className: "px-2.5 py-1 text-right font-normal", children: "pass" }), _jsx("th", { className: "px-2.5 py-1 text-right font-normal", children: "fail" })] }) }), _jsxs("tbody", { children: [_jsxs("tr", { className: "border-b border-rule-soft", children: [_jsx("td", { className: "px-2.5 py-1 text-ink-2", children: "proposed" }), _jsx("td", { className: "px-2.5 py-1", children: _jsx("div", { className: "h-2.5 bg-[var(--neutral-bar)]", style: { width: "100%" } }) }), _jsx("td", { className: "px-2.5 py-1 text-right text-ink", children: f.proposed }), _jsx("td", { className: "px-2.5 py-1 text-right text-ink-3" })] }), f.rows.map((r, i) => {
                                const last = i === f.rows.length - 1;
                                return (_jsxs("tr", { className: "border-b border-rule-soft", children: [_jsx("td", { className: `px-2.5 py-1 whitespace-nowrap ${last ? "font-bold text-accent" : "text-ink-2"}`, children: r.label }), _jsx("td", { className: "px-2.5 py-1", children: _jsx("div", { className: "h-2.5 transition-[width] duration-300", style: {
                                                    width: `${(r.passed / max) * 100}%`,
                                                    background: last ? "var(--accent-bar)" : "var(--neutral-bar)",
                                                } }) }), _jsx("td", { className: `px-2.5 py-1 text-right ${last ? "font-bold text-accent" : "text-ink"}`, children: r.passed }), _jsx("td", { className: `px-2.5 py-1 text-right ${r.failed ? "text-bad" : "text-ink-3"}`, children: r.failed ? `-${r.failed}` : "·" })] }, r.label));
                            })] })] }), _jsxs("div", { className: "grid grid-cols-2 gap-x-4 gap-y-0.5 border-t border-rule px-2.5 py-2 sm:grid-cols-4", children: [_jsxs("span", { className: "text-good", children: ["\u2713 proven ", f.proven] }), _jsxs("span", { className: "text-warn", children: ["~ tested ", f.tested] }), _jsxs("span", { className: "col-span-2 text-ink-3 sm:text-right", children: [fn.stopped ? "stopped early · " : "", fn.stalls.length, " ", fn.stalls.length === 1 ? "stall" : "stalls", " \u2192 reasoning tier"] })] }), _jsxs("div", { className: "border-t border-rule-soft px-2.5 py-2 text-ink-3", children: [_jsx("div", { className: "mb-1", children: "where time goes" }), _jsx("div", { className: "flex h-2.5 w-full overflow-hidden bg-panel-head", children: [
                            ["model", t.model, "var(--accent-bar)"],
                            ["harness", t.harness, "var(--neutral-bar)"],
                            ["verifier", t.verifier, "var(--ink-2)"],
                            ["orchestrator", t.orchestrator, "var(--rule)"],
                        ].map(([k, v, c]) => (_jsx("div", { title: `${k}: ${share(v)}`, style: { width: total ? `${(v / total) * 100}%` : 0, background: c } }, k))) }), _jsxs("div", { className: "tabular mt-1 flex flex-wrap gap-x-4", children: [_jsxs("span", { children: ["model ", share(t.model)] }), _jsxs("span", { children: ["harness ", share(t.harness)] }), _jsxs("span", { children: ["verifier ", share(t.verifier)] }), _jsxs("span", { children: ["orchestrator ", share(t.orchestrator)] })] })] })] }));
}
