import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { CandidateFeed } from "./CandidateFeed";
import { Funnel } from "./Funnel";
import { Playback } from "./Playback";
import { ProgressChart } from "./ProgressChart";
import { Usage } from "./Usage";
/** The search as it happened: `fn` is the state at the playback cursor,
 * `full` the finished function (fixes chart axes so they don't move). */
export function SearchView({ fn, full, replay, live, }) {
    return (_jsxs("div", { className: "space-y-1.5", children: [_jsx(Playback, { replay: replay, live: live }), _jsxs("div", { className: "grid items-start gap-1.5 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]", children: [_jsx(Funnel, { fn: fn }), _jsxs("div", { className: "min-w-0 space-y-1.5", children: [_jsx(ProgressChart, { fn: fn, full: full }), _jsx(Usage, { fn: fn })] })] }), _jsx(CandidateFeed, { fn: fn })] }));
}
