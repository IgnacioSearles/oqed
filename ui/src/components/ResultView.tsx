import { useMemo } from "react";
import { diffLines, type LineMark } from "../diff";
import { type FunctionView, bestCompiler, headlineSpeedup, instructionLines, modelLabel } from "../model";
import { AsmColumn } from "./AsmColumn";
import { LatencyBars, type MeasurementRow } from "./LatencyBars";
import { Panel } from "./Panel";
import { StatusBadge } from "./StatusBadge";

const same = (n: number): LineMark[] => new Array(n).fill("same");
const countInsns = (lines: string[]) => lines.filter((l) => !l.endsWith(":")).length;

export function ResultView({ fn }: { fn: FunctionView }) {
  const best = fn.best;
  const faster = bestCompiler(fn);
  const speedup = headlineSpeedup(fn);
  const bar = faster.baseline.cycles?.median;

  const listings = useMemo(() => {
    const oqed = best ? instructionLines(best.asm) : [];
    const listing = (asm: string) => {
      const lines = instructionLines(asm);
      return { lines, marks: best ? diffLines(lines, oqed).a : same(lines.length) };
    };
    const cols = [
      { key: "clang", title: "clang -O3", ...listing(fn.clang.asm), base: fn.clang },
      { key: "gcc", title: "gcc -O3", ...listing(fn.gcc.asm), base: fn.gcc },
    ];
    if (fn.reference) {
      cols.push({ key: "ref", title: "hand-written", ...listing(fn.reference.asm), base: fn.reference });
    }
    // oqed's `+` lines are relative to the faster compiler's output.
    const oqedMarks = best ? diffLines(instructionLines(faster.baseline.asm), oqed).b : [];
    return { cols, oqed, oqedMarks };
  }, [fn, best, faster]);

  const rows: MeasurementRow[] = [
    ...listings.cols
      .filter((c) => c.base.cycles)
      .map((c) => ({
        key: c.key,
        label: c.title,
        cycles: c.base.cycles!,
        mcaLatency: c.base.mca_latency,
        instructions: countInsns(c.lines),
        speedup: bar ? bar / c.base.cycles!.median : null,
      })),
    ...(best?.cycles
      ? [
          {
            key: "oqed",
            label: "oqed",
            cycles: best.cycles,
            mcaLatency: best.mcaLatency,
            instructions: countInsns(listings.oqed),
            speedup,
            emphasis: true,
          },
        ]
      : []),
  ];

  const nCols = listings.cols.length + (best ? 1 : 0);

  return (
    <div className="space-y-1.5">
      <Panel title={fn.id} right={<StatusBadge status={best?.status} reason={best?.statusReason} />}>
        <div className="grid gap-x-6 gap-y-2 px-2.5 py-2 md:grid-cols-[auto_minmax(0,1fr)]">
          <div className="text-ink-3">signature</div>
          <div className="break-all text-ink-2">{fn.signature}</div>
          <div className="text-ink-3">result</div>
          <div>
            {best && speedup ? (
              <span>
                <span className="text-xl font-bold text-accent">{speedup.toFixed(2)}×</span>{" "}
                <span className="text-ink-2">lower latency than {faster.name} -O3 (the faster compiler)</span>
              </span>
            ) : (
              <span className="text-ink-2">
                no candidate beat {faster.name} -O3 by more than the measurement noise ({fn.candidates.length} tried)
              </span>
            )}
          </div>
          {best && (
            <>
              <div className="text-ink-3">trick</div>
              <div className="text-ink">{best.explanation || "—"}</div>
              <div className="text-ink-3">found by</div>
              <div className="text-ink-2">
                {modelLabel(best.model)} ({best.tier === "reasoning" ? "reasoning" : "volume"} tier), candidate{" "}
                {best.n} of {fn.candidates.length}
              </div>
            </>
          )}
        </div>
      </Panel>

      <Panel title="C source" right="reference semantics" bodyClassName="overflow-x-auto">
        <pre className="px-2.5 py-2 leading-[1.6] text-ink-2">{fn.source.trim()}</pre>
      </Panel>

      <div
        className={`grid gap-1.5 md:grid-cols-2 ${nCols >= 4 ? "xl:grid-cols-4" : nCols === 3 ? "xl:grid-cols-3" : ""}`}
      >
        {listings.cols.map((c) => (
          <AsmColumn
            key={c.key}
            title={c.title}
            note={c.key === faster.name ? "faster compiler" : undefined}
            lines={c.lines}
            marks={c.marks}
          />
        ))}
        {best && (
          <AsmColumn
            title="oqed"
            note={modelLabel(best.model)}
            lines={listings.oqed}
            marks={listings.oqedMarks}
            emphasis
          />
        )}
      </div>

      {rows.length > 0 && <LatencyBars rows={rows} />}
    </div>
  );
}
