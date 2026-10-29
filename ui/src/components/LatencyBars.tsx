import { useState } from "react";
import type { Cycles } from "../events";
import { Panel } from "./Panel";

export interface MeasurementRow {
  key: string;
  label: string;
  cycles: Cycles;
  mcaLatency?: number | null;
  instructions: number;
  speedup?: number | null; // relative to the faster compiler
  emphasis?: boolean;
}

/** Profiler-style table of measurements with an inline latency bar: oqed in
 * the accent color, compilers in neutral gray, whisker = interquartile range. */
export function LatencyBars({ rows, unit, project = false }: { rows: MeasurementRow[]; unit: string; project?: boolean }) {
  const [hover, setHover] = useState<string | null>(null);
  const max = Math.max(...rows.map((r) => r.cycles.median + (r.cycles.spread || 0) / 2)) * 1.05;
  const pct = (v: number) => `${(v / max) * 100}%`;

  return (
    <Panel
      title="Measurements"
      right={project ? `${unit}, this function replaced · lower is faster` : `latency in ${unit} · lower is faster`}
      bodyClassName="overflow-x-auto"
    >
      <table className="tabular w-full min-w-[40rem] border-collapse">
        <thead>
          <tr className="border-b border-rule-soft text-left text-ink-3">
            <th className="px-2.5 py-1 font-normal">code</th>
            <th className="px-2.5 py-1 text-right font-normal">median</th>
            <th className="px-2.5 py-1 text-right font-normal">IQR</th>
            <th className="w-[40%] px-2.5 py-1 font-normal">{project ? "run time" : "latency"}</th>
            {!project && <th className="px-2.5 py-1 text-right font-normal">llvm-mca</th>}
            <th className="px-2.5 py-1 text-right font-normal">instrs</th>
            <th className="px-2.5 py-1 text-right font-normal">speedup</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.key}
              className={`border-b border-rule-soft last:border-b-0 ${r.emphasis ? "font-bold text-accent" : "text-ink-2"} ${
                hover === r.key ? "bg-select" : ""
              }`}
              onMouseEnter={() => setHover(r.key)}
              onMouseLeave={() => setHover(null)}
            >
              <td className="px-2.5 py-1.5 whitespace-nowrap">{r.label}</td>
              <td className="px-2.5 py-1.5 text-right">{r.cycles.median.toFixed(2)}</td>
              <td
                className="px-2.5 py-1.5 text-right text-ink-3"
                title={Number.isFinite(r.cycles.spread) ? undefined : "not logged by this run"}
              >
                {Number.isFinite(r.cycles.spread) ? `±${r.cycles.spread.toFixed(2)}` : "—"}
              </td>
              <td className="px-2.5 py-1.5">
                <div className="relative h-3">
                  <div
                    className="absolute inset-y-0 left-0"
                    style={{
                      width: pct(r.cycles.median),
                      background: r.emphasis ? "var(--accent-bar)" : "var(--neutral-bar)",
                    }}
                  />
                  {r.cycles.spread > 0 && (
                    <div
                      className="absolute top-1/2 h-2 -translate-y-1/2 border-x border-ink-2"
                      style={{ left: pct(r.cycles.median - r.cycles.spread / 2), width: pct(r.cycles.spread) }}
                    >
                      <div className="absolute top-1/2 h-px w-full bg-ink-2" />
                    </div>
                  )}
                </div>
              </td>
              {!project && <td className="px-2.5 py-1.5 text-right">{r.mcaLatency || "—"}</td>}
              <td className="px-2.5 py-1.5 text-right">{r.instructions}</td>
              <td className="px-2.5 py-1.5 text-right">{r.speedup ? `${r.speedup.toFixed(2)}×` : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}
