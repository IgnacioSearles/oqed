import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { SPEEDS } from "../replay";
import { Panel } from "./Panel";
const clock = (s) => {
    const m = Math.floor(s / 60);
    return `${String(m).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
};
export function Playback({ replay, live }) {
    const { cursor, start, end, playing, speed } = replay;
    const btn = "border border-rule px-2 leading-6";
    return (_jsx(Panel, { title: "Playback", right: live ? (_jsx("span", { className: "text-good", children: "\u25CF live" })) : (_jsxs("span", { className: "tabular", children: [clock(cursor - start), " / ", clock(end - start)] })), children: _jsxs("div", { className: "flex flex-wrap items-center gap-2 px-2.5 py-2", children: [_jsx("button", { onClick: playing ? replay.pause : replay.play, className: `${btn} w-20 bg-accent-bg font-bold text-accent`, children: playing ? "pause" : replay.atEnd ? "replay" : "play" }), _jsx("div", { className: "flex", children: SPEEDS.map((s) => (_jsxs("button", { onClick: () => replay.setSpeed(s), "aria-pressed": speed === s, className: `${btn} -ml-px first:ml-0 ${speed === s ? "bg-select font-bold text-ink" : "bg-panel text-ink-3"}`, children: [s, "\u00D7"] }, s))) }), _jsx("input", { type: "range", "aria-label": "playback position", min: start, max: end, step: 0.1, value: cursor, onChange: (e) => replay.seek(Number(e.target.value)), className: "min-w-40 flex-1 accent-[var(--accent)]" })] }) }));
}
