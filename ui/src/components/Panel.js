import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/** A bordered box with a title bar, the basic building block of the layout. */
export function Panel({ title, right, children, className = "", bodyClassName = "", accent = false, }) {
    return (_jsxs("section", { className: `flex min-w-0 flex-col border border-rule bg-panel ${className}`, children: [_jsxs("header", { className: `flex flex-wrap items-center justify-between gap-x-3 border-b border-rule px-2.5 py-1 ${accent ? "bg-accent-bg" : "bg-panel-head"}`, children: [_jsx("h2", { className: `font-bold break-all ${accent ? "text-accent" : "text-ink"}`, children: title }), right && _jsx("div", { className: "text-ink-3", children: right })] }), _jsx("div", { className: `min-w-0 flex-1 ${bodyClassName}`, children: children })] }));
}
