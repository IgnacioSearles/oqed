import type { Candidate, FunctionView } from "../model";
import { modelLabel } from "../model";
import { Panel } from "./Panel";

const STAGE: Record<string, string> = {
  parse: "unparseable",
  duplicate: "duplicate",
  assemble: "assembler",
  isa: "ISA",
  test: "test",
  estimate: "llvm-mca",
  benchmark: "not faster",
  verify: "proof",
};

function outcome(c: Candidate) {
  if (c.rejected) {
    const ce = c.rejected.counterexample;
    const args = ce ? Object.entries(ce).map(([k, v]) => `${k}=${fmt(v)}`) : [];
    return (
      <span className="text-ink-3">
        <span className="text-bad">✗ {STAGE[c.rejected.stage] ?? c.rejected.stage}</span>{" "}
        {c.rejected.stage === "benchmark" ? "" : c.rejected.reason}
        {args.length > 0 && <span className="text-ink-2"> · counterexample {args.join(", ")}</span>}
        {c.rejected.stage === "benchmark" && c.cycles && <span> {c.cycles.median.toFixed(2)} ticks</span>}
      </span>
    );
  }
  if (c.status === "proven") {
    return (
      <span className="text-good">
        ✓ proven <span className="text-ink-2">{c.cycles?.median.toFixed(2)} ticks</span>
      </span>
    );
  }
  if (c.status === "tested_only") {
    return (
      <span className="text-warn" title={c.statusReason ?? undefined}>
        ~ tested <span className="text-ink-2">{c.cycles?.median.toFixed(2)} ticks</span>
      </span>
    );
  }
  return <span className="text-ink-3">… in flight</span>;
}

/** Large values read better in hex (0x80000000 rather than 2147483648). */
function fmt(v: number): string {
  return Math.abs(v) >= 0x10000 ? `0x${(v >>> 0).toString(16)}` : String(v);
}

/** Newest first. Stall events appear as separator rows where the search
 * handed over to the reasoning tier. */
export function CandidateFeed({ fn }: { fn: FunctionView }) {
  type Row = { kind: "cand"; c: Candidate } | { kind: "stall"; rounds: number; key: string };
  const rows: Row[] = [];
  let s = 0;
  for (const c of fn.candidates) {
    while (s < fn.stalls.length && fn.stalls[s].afterN < c.n) {
      rows.push({ kind: "stall", rounds: fn.stalls[s].rounds, key: `stall-${s}` });
      s++;
    }
    rows.push({ kind: "cand", c });
  }
  for (; s < fn.stalls.length; s++) rows.push({ kind: "stall", rounds: fn.stalls[s].rounds, key: `stall-${s}` });
  rows.reverse();
  const done = fn.stopped;

  return (
    <Panel title="Candidates" right="newest first" bodyClassName="max-h-[34rem] overflow-y-auto">
      <ol>
        {done && (
          <li className="border-b border-rule-soft bg-panel-head px-2.5 py-1 text-ink-2">
            ■ search stopped early: {done.reason}
          </li>
        )}
        {rows.map((r) =>
          r.kind === "stall" ? (
            <li key={r.key} className="border-b border-rule-soft bg-panel-head px-2.5 py-1 text-ink-2">
              ◇ stalled for {r.rounds} without improvement · calling the reasoning tier
            </li>
          ) : (
            <li
              key={r.c.id}
              className={`grid grid-cols-[2.5rem_minmax(0,1fr)] gap-x-2 border-b border-rule-soft px-2.5 py-1.5 last:border-b-0 ${
                fn.best?.id === r.c.id ? "bg-accent-bg" : ""
              }`}
            >
              <span className="tabular text-ink-3">#{r.c.n}</span>
              <span className="min-w-0 space-y-0.5">
                <span className="block text-ink-3">
                  {r.c.tier === "reasoning" ? "◇ " : ""}
                  {modelLabel(r.c.model)}
                  {fn.best?.id === r.c.id && <span className="font-bold text-accent"> · best</span>}
                </span>
                <span className="block text-ink">{r.c.explanation || <span className="text-ink-3">(no explanation)</span>}</span>
                <span className="block break-words">{outcome(r.c)}</span>
              </span>
            </li>
          ),
        )}
      </ol>
    </Panel>
  );
}
