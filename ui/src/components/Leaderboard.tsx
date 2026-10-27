import type { RunIndexEntry } from "../events";
import { type FunctionView, headlineSpeedup } from "../model";
import { Panel } from "./Panel";
import { StatusBadge } from "./StatusBadge";

/** Every function in the run, best speedup first. Doubles as the function picker. */
export function Leaderboard({
  functions,
  selected,
  onSelect,
}: {
  functions: FunctionView[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const rows = [...functions].sort((a, b) => (headlineSpeedup(b) ?? 0) - (headlineSpeedup(a) ?? 0));
  return (
    <Panel title="Functions" right={`${functions.length}`}>
      <ul>
        {rows.map((fn) => {
          const s = headlineSpeedup(fn);
          const active = fn.id === selected;
          return (
            <li key={fn.id} className="border-b border-rule-soft last:border-b-0">
              <button
                onClick={() => onSelect(fn.id)}
                aria-current={active ? "true" : undefined}
                className={`block w-full space-y-1 px-2.5 py-1.5 text-left ${
                  active ? "bg-select" : "hover:bg-panel-head"
                }`}
              >
                <span className={`block break-all ${active ? "font-bold text-ink" : "text-ink-2"}`}>{fn.id}</span>
                <span className="flex items-center justify-between gap-2">
                  <StatusBadge status={fn.best?.status} short />
                  <span className={`tabular ${s ? "font-bold text-accent" : "text-ink-3"}`}>
                    {s ? `${s.toFixed(2)}×` : "—"}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

export function RunList({
  runs,
  selected,
  onSelect,
}: {
  runs: RunIndexEntry[];
  selected: string | undefined;
  onSelect: (id: string) => void;
}) {
  return (
    <Panel title="Runs" right={`${runs.length}`} bodyClassName="max-h-56 overflow-y-auto">
      <ul>
        {runs.map((r) => {
          const active = r.id === selected;
          const d = new Date(r.started * 1000);
          const pad = (n: number) => String(n).padStart(2, "0");
          const when = `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
          return (
            <li key={r.id} className="border-b border-rule-soft last:border-b-0">
              <button
                onClick={() => onSelect(r.id)}
                aria-current={active ? "true" : undefined}
                className={`flex w-full items-baseline justify-between gap-2 px-2.5 py-1 text-left ${
                  active ? "bg-select font-bold text-ink" : "text-ink-2 hover:bg-panel-head"
                }`}
              >
                <span className="tabular">{when}</span>
                <span className="shrink-0 text-ink-3">
                  {r.functions.length}fn {r.fake ? "synthetic" : r.demo ? "demo" : "local"}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}
