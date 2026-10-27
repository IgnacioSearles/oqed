import { type FunctionView, funnel } from "../model";
import { Panel } from "./Panel";

/** Candidates surviving each pipeline stage, in the order the search runs them. */
export function Funnel({ fn }: { fn: FunctionView }) {
  const f = funnel(fn);
  const max = Math.max(1, f.proposed);
  const t = fn.timing;
  const total = t.model + t.harness + t.verifier + t.orchestrator;
  const share = (v: number) => {
    if (!total) return "—";
    const pct = (v / total) * 100;
    return v > 0 && pct < 1 ? "<1%" : `${Math.round(pct)}%`;
  };

  return (
    <Panel title="Funnel" right={`${f.proposed} proposed`}>
      <table className="tabular w-full border-collapse">
        <thead>
          <tr className="border-b border-rule-soft text-left text-ink-3">
            <th className="px-2.5 py-1 font-normal">stage</th>
            <th className="w-[45%] px-2.5 py-1 font-normal" />
            <th className="px-2.5 py-1 text-right font-normal">pass</th>
            <th className="px-2.5 py-1 text-right font-normal">fail</th>
          </tr>
        </thead>
        <tbody>
          <tr className="border-b border-rule-soft">
            <td className="px-2.5 py-1 text-ink-2">proposed</td>
            <td className="px-2.5 py-1">
              <div className="h-2.5 bg-[var(--neutral-bar)]" style={{ width: "100%" }} />
            </td>
            <td className="px-2.5 py-1 text-right text-ink">{f.proposed}</td>
            <td className="px-2.5 py-1 text-right text-ink-3" />
          </tr>
          {f.rows.map((r, i) => {
            const last = i === f.rows.length - 1;
            return (
              <tr key={r.label} className="border-b border-rule-soft">
                <td className={`px-2.5 py-1 whitespace-nowrap ${last ? "font-bold text-accent" : "text-ink-2"}`}>
                  {r.label}
                </td>
                <td className="px-2.5 py-1">
                  <div
                    className="h-2.5 transition-[width] duration-300"
                    style={{
                      width: `${(r.passed / max) * 100}%`,
                      background: last ? "var(--accent-bar)" : "var(--neutral-bar)",
                    }}
                  />
                </td>
                <td className={`px-2.5 py-1 text-right ${last ? "font-bold text-accent" : "text-ink"}`}>{r.passed}</td>
                <td className={`px-2.5 py-1 text-right ${r.failed ? "text-bad" : "text-ink-3"}`}>
                  {r.failed ? `-${r.failed}` : "·"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 border-t border-rule px-2.5 py-2 sm:grid-cols-4">
        <span className="text-good">✓ proven {f.proven}</span>
        <span className="text-warn">~ tested {f.tested}</span>
        <span className="col-span-2 text-ink-3 sm:text-right">{fn.stalls.length} {fn.stalls.length === 1 ? "stall" : "stalls"} → reasoning tier</span>
      </div>
      <div className="border-t border-rule-soft px-2.5 py-2 text-ink-3">
        <div className="mb-1">where time goes</div>
        <div className="flex h-2.5 w-full overflow-hidden bg-panel-head">
          {(
            [
              ["model", t.model, "var(--accent-bar)"],
              ["harness", t.harness, "var(--neutral-bar)"],
              ["verifier", t.verifier, "var(--ink-2)"],
              ["orchestrator", t.orchestrator, "var(--rule)"],
            ] as const
          ).map(([k, v, c]) => (
            <div key={k} title={`${k}: ${share(v)}`} style={{ width: total ? `${(v / total) * 100}%` : 0, background: c }} />
          ))}
        </div>
        <div className="tabular mt-1 flex flex-wrap gap-x-4">
          <span>model {share(t.model)}</span>
          <span>harness {share(t.harness)}</span>
          <span>verifier {share(t.verifier)}</span>
          <span>orchestrator {share(t.orchestrator)}</span>
        </div>
      </div>
    </Panel>
  );
}
