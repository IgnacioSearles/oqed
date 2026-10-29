import type { RunIndexEntry } from "../events";
import { fmtUsd } from "../model";
import { Panel } from "./Panel";

/** Every project oqed was pointed at: the latest measured run of each. */
export function ProjectsTable({ runs, onOpen }: { runs: RunIndexEntry[]; onOpen: (run: string) => void }) {
  const latest = new Map<string, RunIndexEntry>();
  for (const r of runs) {
    if (!r.project || !r.result) continue;
    const cur = latest.get(r.project);
    const measured = r.result.speedup != null;
    // Prefer measured runs, then the newest.
    if (!cur || (measured && cur.result?.speedup == null) || (measured === (cur.result?.speedup != null) && r.started > cur.started)) {
      latest.set(r.project, r);
    }
  }
  const rows = [...latest.values()].sort((a, b) => (b.result?.speedup ?? 0) - (a.result?.speedup ?? 0));
  const max = Math.max(1.1, ...rows.map((r) => r.result?.speedup ?? 1));
  return (
    <Panel title="Projects" right="oqed pointed at the repository, no functions picked · latest measured run of each" bodyClassName="overflow-x-auto">
      {rows.length === 0 ? (
        <p className="px-2.5 py-2 text-ink-3">No whole-project runs yet: `oqed optimize path/to/project`.</p>
      ) : (
        <table className="tabular w-full min-w-[46rem] border-collapse">
          <thead>
            <tr className="border-b border-rule-soft text-left text-ink-3">
              <th className="px-2.5 py-1 font-normal">project</th>
              <th className="px-2.5 py-1 text-right font-normal">functions</th>
              <th className="px-2.5 py-1 text-right font-normal">searched</th>
              <th className="px-2.5 py-1 font-normal">won</th>
              <th className="w-[26%] px-2.5 py-1 font-normal">whole program</th>
              <th className="px-2.5 py-1 text-right font-normal">spend</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const res = r.result!;
              const s = res.speedup;
              const ok = s != null && res.valid !== false;
              return (
                <tr
                  key={r.id}
                  onClick={() => onOpen(r.id)}
                  className="cursor-pointer border-b border-rule-soft text-ink-2 last:border-b-0 hover:bg-panel-head"
                >
                  <td className="px-2.5 py-1.5 font-bold text-ink">{r.project}</td>
                  <td className="px-2.5 py-1.5 text-right">
                    {res.searchable}
                    <span className="text-ink-3"> / {res.functions}</span>
                  </td>
                  <td className="px-2.5 py-1.5 text-right">{r.functions.length}</td>
                  <td className="max-w-[16rem] truncate px-2.5 py-1.5" title={res.won.join(", ")}>
                    {res.won.length ? (
                      <>
                        <span className="font-bold text-good">{res.won.length}</span>{" "}
                        <span className="text-ink-3">{res.won.join(", ")}</span>
                      </>
                    ) : (
                      <span className="text-ink-3">none</span>
                    )}
                  </td>
                  <td className="px-2.5 py-1.5">
                    {s == null ? (
                      <span className="text-ink-3">not measured</span>
                    ) : (
                      <span className="flex items-center gap-2">
                        <span className="relative h-3 flex-1 border-l border-rule">
                          <span
                            className={`absolute inset-y-0 left-0 rounded-r-[3px] ${ok && s > 1 ? "bg-good" : "bg-[var(--neutral-bar)]"}`}
                            style={{ width: `${(Math.max(0, s - 1) / (max - 1)) * 100}%`, minWidth: 2 }}
                          />
                        </span>
                        <span className={`w-14 text-right font-bold ${ok ? "text-ink" : "text-bad"}`}>{s.toFixed(3)}×</span>
                        {res.faster_runs != null && res.runs != null && (
                          <span className="w-12 text-ink-3" title="faster in this many of the paired runs">
                            {res.faster_runs}/{res.runs}
                          </span>
                        )}
                      </span>
                    )}
                  </td>
                  <td className="px-2.5 py-1.5 text-right">{fmtUsd(res.cost)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <p className="border-t border-rule-soft px-2.5 py-1.5 text-ink-3">
        Whole program: the project's own workload, plain {"-O3"} build vs the same build through oqed-cc, alternating runs; bar
        length is the gain over 1.0×. Functions: what oqed can search, of all it found.
      </p>
    </Panel>
  );
}
