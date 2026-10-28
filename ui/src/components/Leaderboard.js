import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { headlineSpeedup } from "../model";
import { Panel } from "./Panel";
import { StatusBadge } from "./StatusBadge";
/** Every function in the run, best speedup first. Doubles as the function picker. */
export function Leaderboard({ functions, selected, onSelect, }) {
    const rows = [...functions].sort((a, b) => (headlineSpeedup(b) ?? 0) - (headlineSpeedup(a) ?? 0));
    return (_jsx(Panel, { title: "Functions", right: `${functions.length}`, children: _jsx("ul", { children: rows.map((fn) => {
                const s = headlineSpeedup(fn);
                const active = fn.id === selected;
                return (_jsx("li", { className: "border-b border-rule-soft last:border-b-0", children: _jsxs("button", { onClick: () => onSelect(fn.id), "aria-current": active ? "true" : undefined, className: `block w-full space-y-1 px-2.5 py-1.5 text-left ${active ? "bg-select" : "hover:bg-panel-head"}`, children: [_jsx("span", { className: `block break-all ${active ? "font-bold text-ink" : "text-ink-2"}`, children: fn.id }), _jsxs("span", { className: "flex items-center justify-between gap-2", children: [_jsx(StatusBadge, { status: fn.best?.status, short: true }), _jsx("span", { className: `tabular ${s ? "font-bold text-accent" : "text-ink-3"}`, children: s ? `${s.toFixed(2)}×` : "—" })] })] }) }, fn.id));
            }) }) }));
}
export function RunList({ runs, selected, onSelect, }) {
    return (_jsx(Panel, { title: "Runs", right: `${runs.length}`, bodyClassName: "max-h-56 overflow-y-auto", children: _jsx("ul", { children: runs.map((r) => {
                const active = r.id === selected;
                const d = new Date(r.started * 1000);
                const pad = (n) => String(n).padStart(2, "0");
                const when = `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
                return (_jsx("li", { className: "border-b border-rule-soft last:border-b-0", children: _jsxs("button", { onClick: () => onSelect(r.id), "aria-current": active ? "true" : undefined, className: `flex w-full items-baseline justify-between gap-2 px-2.5 py-1 text-left ${active ? "bg-select font-bold text-ink" : "text-ink-2 hover:bg-panel-head"}`, children: [_jsx("span", { className: "tabular", children: when }), _jsxs("span", { className: "shrink-0 text-ink-3", children: [r.functions.length, "fn ", r.fake ? "synthetic" : r.demo ? "demo" : "local"] })] }) }, r.id));
            }) }) }));
}
