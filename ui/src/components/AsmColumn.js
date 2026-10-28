import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Panel } from "./Panel";
/** One assembly listing with a diff gutter. Compiler listings mark lines oqed
 * dropped with `-`; the oqed listing marks lines it introduced with `+`. */
export function AsmColumn({ title, note, lines, marks, emphasis = false, }) {
    return (_jsx(Panel, { title: title, right: note, accent: emphasis, bodyClassName: "overflow-x-auto", children: _jsx("pre", { className: "py-1 leading-[1.6]", children: lines.map((line, i) => {
                const changed = marks[i] === "changed";
                const label = line.endsWith(":");
                let row = "text-ink-2";
                let gutter = " ";
                if (changed && emphasis) {
                    row = "bg-accent-bg text-accent font-medium";
                    gutter = "+";
                }
                else if (changed) {
                    row = "bg-minus-bg text-ink-3";
                    gutter = "-";
                }
                else if (emphasis) {
                    row = "text-ink";
                }
                return (_jsxs("div", { className: `flex pr-3 ${row}`, children: [_jsx("span", { className: "tabular w-9 shrink-0 pr-2 text-right text-ink-3 select-none", children: i + 1 }), _jsx("span", { className: "w-4 shrink-0 select-none", children: gutter }), _jsx("span", { className: label ? "" : "pl-3", children: line })] }, i));
            }) }) }));
}
