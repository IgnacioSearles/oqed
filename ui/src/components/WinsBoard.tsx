import { useState } from "react";
import type { RunIndexEntry, Win } from "../events";
import { modelLabel } from "../model";
import { Panel } from "./Panel";
import { StatusBadge } from "./StatusBadge";

interface Row extends Win {
  run: RunIndexEntry;
  where: string; // the project, or "benchmark" for hand-picked functions
}

/** The latest measured run of each project (the lock as it was last measured). */
function measured(runs: RunIndexEntry[]): Map<string, RunIndexEntry> {
  const out = new Map<string, RunIndexEntry>();
  for (const r of runs) {
    if (!r.project || r.result?.speedup == null) continue;
    const cur = out.get(r.project);
    if (!cur || r.started > cur.started) out.set(r.project, r);
  }
  return out;
}

/** A project win the last whole-program measurement didn't apply: its lock
 * entry was dropped (re-checked and not faster) or replaced. */
function dropped(w: Win, report: RunIndexEntry | undefined): boolean {
  const at = report?.result?.appliedAt;
  if (!at || !w.location) return false;
  const file = w.location.replace(/:\d+$/, "");
  return !at.some((a) => {
    const i = a.lastIndexOf(":");
    const fn = a.slice(i + 1);
    return (a.slice(0, i) === file || a.slice(0, i).endsWith(`/${file}`)) && w.function.split(".").includes(fn);
  });
}

/** Every function oqed made faster, across all runs: each one's best result.
 * Function speedups (the asm inlined into a loop, against the faster of
 * clang and gcc -O3) and whole-program speedups (the project's own
 * benchmark, a paired comparison) are different measures and say so. */
export function WinsBoard({ runs, onOpen }: { runs: RunIndexEntry[]; onOpen: (run: string, fn: string) => void }) {
  const [proven, setProven] = useState(false);
  const [measure, setMeasure] = useState<"all" | "function" | "program">("all");
  const reports = measured(runs);
  const best = new Map<string, Row>();
  let droppedCount = 0;
  for (const run of runs) {
    if (run.fake) continue;
    for (const w of run.wins ?? []) {
      if (w.speedup <= 1) continue;
      // Judged by a project's benchmark in a run that doesn't say which
      // project (older logs): no way to tell whether it held up.
      if (w.program && !run.project) continue;
      if (run.project && dropped(w, reports.get(run.project))) {
        droppedCount++;
        continue;
      }
      const where = run.project ?? "benchmark";
      const key = `${where}\0${w.function}`;
      const cur = best.get(key);
      const better =
        !cur ||
        (w.status === "proven" && cur.status !== "proven") ||
        ((w.status === "proven") === (cur.status === "proven") && w.speedup > cur.speedup);
      if (better) best.set(key, { ...w, run, where });
    }
  }
  const rows = [...best.values()]
    .filter((r) => !proven || r.status === "proven")
    .filter((r) => measure === "all" || (measure === "program") === r.program)
    .sort((a, b) => b.speedup - a.speedup);
  const toggle = (on: boolean) =>
    `-ml-px border border-rule px-2 leading-6 first:ml-0 ${on ? "bg-select font-bold text-ink" : "bg-panel text-ink-3 hover:text-ink"}`;

  return (
    <Panel
      title="Leaderboard"
      right={
        <span className="flex flex-wrap items-center gap-2">
          <span className="flex" role="group" aria-label="measured on">
            {(["all", "function", "program"] as const).map((m) => (
              <button key={m} className={toggle(measure === m)} aria-pressed={measure === m} onClick={() => setMeasure(m)}>
                {m === "program" ? "whole program" : m}
              </button>
            ))}
          </span>
          <button className={toggle(proven)} aria-pressed={proven} onClick={() => setProven(!proven)}>
            proven only
          </button>
        </span>
      }
      bodyClassName="overflow-x-auto"
    >
      {rows.length === 0 ? (
        <p className="px-2.5 py-2 text-ink-3">No wins yet.</p>
      ) : (
        <table className="tabular w-full min-w-[44rem] border-collapse">
          <thead>
            <tr className="border-b border-rule-soft text-left text-ink-3">
              <th className="px-2.5 py-1 text-right font-normal">#</th>
              <th className="px-2.5 py-1 font-normal">function</th>
              <th className="px-2.5 py-1 font-normal">where</th>
              <th className="px-2.5 py-1 text-right font-normal">speedup</th>
              <th className="px-2.5 py-1 font-normal">measured on</th>
              <th className="px-2.5 py-1 font-normal">status</th>
              <th className="px-2.5 py-1 font-normal">found by</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr
                key={`${r.where}/${r.function}`}
                onClick={() => onOpen(r.run.id, r.function)}
                className="cursor-pointer border-b border-rule-soft text-ink-2 last:border-b-0 hover:bg-panel-head"
              >
                <td className="px-2.5 py-1.5 text-right text-ink-3">{i + 1}</td>
                <td className="max-w-[18rem] truncate px-2.5 py-1.5 font-bold text-ink" title={r.function}>
                  {r.function}
                </td>
                <td className="px-2.5 py-1.5">{r.where}</td>
                <td className="px-2.5 py-1.5 text-right font-bold text-ink">{r.speedup.toFixed(2)}×</td>
                <td className="px-2.5 py-1.5 text-ink-3">{r.program ? "whole program" : "function, inlined"}</td>
                <td className="px-2.5 py-1.5">
                  <StatusBadge status={r.status} short />
                </td>
                <td className="px-2.5 py-1.5 text-ink-3">
                  {r.model ? modelLabel(r.model) : "—"}
                  {r.tier === "reasoning" ? " (reasoning)" : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="border-t border-rule-soft px-2.5 py-1.5 text-ink-3">
        Each function's best result over all runs, proven before tested. Function: the asm inlined into a loop, against the
        faster of clang and gcc {"-O3"}. Whole program: the project's own benchmark, against the C build in alternating runs.
        {droppedCount > 0 &&
          ` ${droppedCount} project ${droppedCount === 1 ? "win is" : "wins are"} left out: the last whole-program measurement didn't include ${droppedCount === 1 ? "it" : "them"} (dropped on re-check).`}
      </p>
    </Panel>
  );
}
