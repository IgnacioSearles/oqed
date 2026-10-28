import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/** Proof status as a boxed label. Color is never the only signal: each state
 * has its own symbol and text. */
export function StatusBadge({ status, reason, short = false, }) {
    const base = "inline-block border px-1.5 leading-5 whitespace-nowrap";
    if (status === "proven") {
        return (_jsxs("span", { className: `${base} border-good bg-good-bg text-good`, title: "Symbolic execution (angr) + Z3: equal to the compiler's output for every ABI-valid input", children: ["\u2713 ", short ? "proven" : "proven equivalent"] }));
    }
    if (status === "tested_only") {
        return (_jsxs("span", { className: `${base} border-warn bg-warn-bg text-warn`, title: reason ? `Not proven: ${reason}` : "Passed differential testing; not proven", children: ["~ ", short ? "tested" : "tested, not proven"] }));
    }
    return _jsx("span", { className: `${base} border-rule-soft text-ink-3`, children: "\u2013 no win" });
}
