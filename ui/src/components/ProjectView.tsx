import { useMemo, useState } from "react";
import type { ProjectReport, RejectStage } from "../events";
import { type RunView, fmtUsd, headlineSpeedup, modelLabel, usage } from "../model";
import { type Tile, type TileState, CAUGHT, pct, tiles as toTiles, timeSplit } from "../project";
import type { Replay } from "../replay";
import { useWidth } from "../useWidth";
import { Panel } from "./Panel";
import { Playback } from "./Playback";
import { STATE_LABEL, Treemap } from "./Treemap";

/** A whole-project run: the codebase map, the measured whole-program result
 * and what the search did. `run` is the state at the playback cursor. */
export function ProjectView({
  run,
  full,
  replay,
  live,
  onOpen,
}: {
  run: RunView;
  full: RunView;
  replay: Replay;
  live: boolean;
  onOpen: (fn: string) => void;
}) {
  const ts = useMemo(() => toTiles(run), [run]);
  const report = full.project?.report ?? null;
  const shownReport = replay.atEnd ? report : null;
  const spend = usage(run.functions.flatMap((f) => f.candidates)).total;
  const counts = useMemo(() => {
    const c: Record<TileState, number> = { won: 0, searching: 0, nowin: 0, queued: 0, idle: 0, skipped: 0 };
    for (const t of ts) c[t.state] += 1;
    return c;
  }, [ts]);
  const split = timeSplit(ts);
  const searchedShare = ts.filter((t) => t.fn.searched).reduce((a, t) => a + t.fn.time, 0);
  const never = ts.filter((t) => t.fn.time === 0).length;
  const [whole, setWhole] = useState(false);

  return (
    <div className="space-y-1.5">
      <Headline run={run} report={shownReport} live={live} measuring={replay.atEnd && full.finished && !report} />

      <div className="grid gap-1.5 sm:grid-cols-2 xl:grid-cols-5">
        <Stat label="functions found" value={String(ts.length)} note={`${ts.length - counts.skipped} oqed can search`} />
        <Stat
          label="searched this run"
          value={String(run.functions.length || ts.filter((t) => t.fn.searched).length)}
          note={`hottest first: ${pct(searchedShare)} of the samples`}
        />
        <Stat
          label="won"
          value={String(counts.won)}
          note={counts.won ? ts.filter((t) => t.state === "won").map((t) => t.fn.function).join(", ") : "none yet"}
          good={counts.won > 0}
        />
        <Stat
          label="candidates"
          value={String(run.functions.reduce((a, f) => a + f.candidates.length, 0))}
          note={`${caughtCount(run)} wrong or ABI-breaking, caught`}
        />
        <Stat label="model spend" value={fmtUsd(spend.cost)} note={models(run)} />
      </div>

      <Panel
        title={`Codebase map · ${run.project?.name}`}
        right={
          <span className="flex items-center gap-3">
            <span className="hidden lg:inline">area = share of CPU samples · click a searched function</span>
            <span className="flex" role="group" aria-label="map scope">
              {[false, true].map((w) => (
                <button
                  key={String(w)}
                  onClick={() => setWhole(w)}
                  aria-pressed={whole === w}
                  className={`-ml-px border border-rule px-2 leading-5 first:ml-0 ${
                    whole === w ? "bg-select font-bold text-ink" : "bg-panel text-ink-3 hover:text-ink"
                  }`}
                >
                  {w ? `whole workload` : "project code"}
                </button>
              ))}
            </span>
          </span>
        }
      >
        <Legend counts={counts} />
        <div className="px-2.5 pb-2.5">
          <Treemap tiles={ts} now={replay.atEnd && !live ? null : replay.cursor} onOpen={onOpen} outside={whole} />
          {never > 0 && (
            <p className="mt-1.5 text-ink-3">
              {!whole && `${pct(split.outside)} of the samples are outside the project (libc, kernel); "whole workload" draws them. `}
              {never} more functions never ran in the workload (no samples), so they aren't drawn.
            </p>
          )}
        </div>
      </Panel>

      <Playback replay={replay} live={live} />

      <div className="grid items-start gap-1.5 xl:grid-cols-2">
        <TimeSplitBar split={split} />
        {report ? <PairedRuns report={report} /> : <Pending live={live} />}
      </div>

      <div className="grid items-start gap-1.5 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <FunctionTable run={run} tiles={ts} onOpen={onOpen} />
        <Caught run={run} />
      </div>
    </div>
  );
}

const models = (run: RunView) =>
  [run.models.volume, run.models.reasoning]
    .filter((m): m is string => Boolean(m))
    .map(modelLabel)
    .filter((m, i, a) => a.indexOf(m) === i)
    .join(" + ");

const caughtCount = (run: RunView) =>
  run.functions.reduce((a, f) => a + f.candidates.filter((c) => c.rejected && CAUGHT.has(c.rejected.stage)).length, 0);

function Headline({
  run,
  report,
  live,
  measuring,
}: {
  run: RunView;
  report: ProjectReport | null;
  live: boolean;
  measuring: boolean;
}) {
  const valid = report && report.valid !== false && report.test_output_identical && report.output_files_identical;
  return (
    <Panel title="Whole program" accent right={report ? `${report.compiler} -O3 build vs the same build with oqed-cc` : undefined}>
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2 px-2.5 py-2.5">
        {report ? (
          <>
            <span className={`text-4xl font-bold tabular ${valid ? "text-accent" : "text-bad"}`}>
              {report.speedup.toFixed(3)}×
            </span>
            <span className="max-w-3xl leading-relaxed text-ink-2">
              {valid ? "faster" : "NOT VALID: the output differs, so this doesn't count"}: median{" "}
              {fmtNum(report.plain.median)} → {fmtNum(report.oqed.median)} {report.unit}
              {report.faster_runs != null && `, faster in ${report.faster_runs} of ${report.plain.runs.length} paired runs`}
              . Same source, no code changes: {report.applied} of {report.entries} lock entries substituted at build time (
              {report.proven} proven, {report.tested} tested). `{report.test}` gives{" "}
              {report.test_output_identical ? "identical output" : "DIFFERENT output"}
              {report.output_files > 0 &&
                ` and ${report.output_files_identical ? "byte-identical" : "DIFFERENT"} output files (${report.output_files})`}
              .
            </span>
          </>
        ) : (
          <span className="text-ink-2">
            {live
              ? `searching ${run.functions.filter((f) => !f.stopped).length} functions…`
              : measuring
                ? "search finished; the whole-program measurement isn't in the log"
                : "the whole-program result comes at the end of the run"}
          </span>
        )}
      </div>
    </Panel>
  );
}

const fmtNum = (v: number) => (v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2));

function Stat({ label, value, note, good }: { label: string; value: string; note: string; good?: boolean }) {
  return (
    <div className="border border-rule bg-panel px-2.5 py-1.5">
      <div className="text-ink-3">{label}</div>
      <div className={`text-2xl font-bold tabular ${good ? "text-good" : "text-ink"}`}>{value}</div>
      <div className="truncate text-ink-3" title={note}>
        {note}
      </div>
    </div>
  );
}

const SWATCH: Record<TileState, string> = {
  won: "bg-good-bg border-good",
  searching: "bg-accent-bg border-accent",
  nowin: "bg-select border-rule-soft",
  queued: "bg-panel border-accent border-dashed",
  idle: "bg-panel border-rule-soft",
  skipped: "oqed-hatch border-rule-soft",
};

function Legend({ counts }: { counts: Record<TileState, number> }) {
  const order: TileState[] = ["won", "searching", "queued", "nowin", "idle", "skipped"];
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 px-2.5 py-1.5 text-ink-2">
      {order
        .filter((s) => counts[s] > 0 || s === "won")
        .map((s) => (
          <span key={s} className="flex items-center gap-1.5">
            <span className={`inline-block h-3 w-4 border-[1.5px] ${SWATCH[s]}`} />
            {STATE_LABEL[s]} <span className="text-ink-3 tabular">{counts[s]}</span>
          </span>
        ))}
      <span className="flex items-center gap-1.5">
        <span className="inline-block h-3 w-4 border-[1.5px] border-bad" />
        red flash: a wrong candidate caught
      </span>
    </div>
  );
}

function TimeSplitBar({ split }: { split: ReturnType<typeof timeSplit> }) {
  const parts: { key: keyof typeof split; label: string; cls: string; note: string }[] = [
    { key: "won", label: "replaced by oqed", cls: "bg-good", note: "functions with a faster version locked" },
    { key: "nowin", label: "searched, no win", cls: "bg-[var(--neutral-bar)]", note: "the compiler's code stayed" },
    { key: "idle", label: "not searched", cls: "bg-panel", note: "searchable, below the cut" },
    { key: "skipped", label: "can't search yet", cls: "oqed-hatch", note: "too big, or arguments oqed can't pass" },
    { key: "outside", label: "outside the project", cls: "oqed-hatch-dense", note: "libc, kernel: out of reach" },
  ];
  return (
    <Panel title="Where the time goes" right="share of CPU samples in the workload">
      <div className="space-y-2 px-2.5 py-2.5">
        <div className="flex h-7 w-full gap-[2px]" role="img" aria-label="time split">
          {parts
            .filter((p) => split[p.key] > 0.0005)
            .map((p) => (
              <div
                key={p.key}
                className={`h-full border border-rule-soft first:rounded-l-[3px] last:rounded-r-[3px] ${p.cls}`}
                style={{ width: `${split[p.key] * 100}%` }}
                title={`${p.label}: ${pct(split[p.key])}`}
              />
            ))}
        </div>
        <table className="w-full tabular">
          <tbody>
            {parts.map((p) => (
              <tr key={p.key} className="text-ink-2">
                <td className="w-5 py-0.5">
                  <span className={`inline-block h-3 w-3.5 border border-rule-soft align-middle ${p.cls}`} />
                </td>
                <td className="py-0.5">{p.label}</td>
                <td className="py-0.5 text-right font-bold text-ink">{pct(split[p.key])}</td>
                <td className="hidden py-0.5 pl-3 text-ink-3 sm:table-cell">{p.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

/** Each alternating pair of runs (plain, then oqed) as a line between the two
 * measurements; green when oqed's was faster. Medians marked. */
function PairedRuns({ report }: { report: ProjectReport }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const a = report.plain.runs;
  const b = report.oqed.runs;
  const all = [...a, ...b];
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  const gutter = width < 480 ? 92 : 132;
  const padX = 12;
  const pad = (hi - lo) * 0.08 || 1;
  const x = (v: number) => gutter + ((v - (lo - pad)) / (hi - lo + 2 * pad)) * (width - gutter - padX);
  const rows = [
    { y: 26, label: `${report.compiler} -O3`, q: report.plain, vals: a, dot: "var(--neutral-bar)" },
    { y: 100, label: "with oqed-cc", q: report.oqed, vals: b, dot: "var(--accent-bar)" },
  ];
  const ticks = niceTicks(lo - pad, hi + pad, Math.max(3, Math.floor((width - gutter) / 100)));
  return (
    <Panel title={`Whole program, ${a.length} paired runs`} right={`${report.unit} · lower is faster`}>
      <div ref={ref} className="px-2.5 py-2">
        {width > 0 && (
          <svg width={width} height={142} className="block overflow-visible" role="img" aria-label="paired runs">
            {ticks.map((t) => (
              <g key={t}>
                <line x1={x(t)} x2={x(t)} y1={10} y2={116} stroke="var(--rule-soft)" />
                <text x={x(t)} y={136} textAnchor="middle" className="fill-[var(--ink-3)] text-[11px]">
                  {fmtNum(t)}
                </text>
              </g>
            ))}
            {a.map((v, i) => {
              const faster = b[i] < v;
              return (
                <line
                  key={i}
                  x1={x(v)}
                  y1={rows[0].y}
                  x2={x(b[i])}
                  y2={rows[1].y}
                  stroke={faster ? "var(--good)" : "var(--bad)"}
                  strokeWidth={hover === i ? 3 : 1.5}
                  opacity={hover == null || hover === i ? 0.8 : 0.2}
                />
              );
            })}
            {rows.map((r) => (
              <g key={r.label}>
                <text x={0} y={r.y - 2} className="fill-[var(--ink-2)] text-[11px]">
                  {r.label}
                </text>
                <text x={0} y={r.y + 12} className="fill-[var(--ink-3)] text-[11px]">
                  median {fmtNum(r.q.median)}
                </text>
                <line x1={x(r.q.median)} x2={x(r.q.median)} y1={r.y - 9} y2={r.y + 9} stroke="var(--ink)" strokeWidth={2} />
                {r.vals.map((v, i) => (
                  <circle
                    key={i}
                    cx={x(v)}
                    cy={r.y}
                    r={hover === i ? 6 : 4.5}
                    fill={r.dot}
                    stroke="var(--panel)"
                    strokeWidth={2}
                    onMouseEnter={() => setHover(i)}
                    onMouseLeave={() => setHover(null)}
                  >
                    <title>
                      pair {i + 1}: {fmtNum(a[i])} → {fmtNum(b[i])} {report.unit}
                    </title>
                  </circle>
                ))}
              </g>
            ))}
          </svg>
        )}
        <p className="text-ink-3">
          {hover != null
            ? `pair ${hover + 1}: ${fmtNum(a[hover])} → ${fmtNum(b[hover])} ${report.unit} (${(a[hover] / b[hover]).toFixed(3)}×)`
            : "builds alternate plain, oqed, plain, … so drift hits both; each line is one pair."}
        </p>
      </div>
    </Panel>
  );
}

function niceTicks(lo: number, hi: number, n: number): number[] {
  const step0 = (hi - lo) / n;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= step0) ?? 10 * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) out.push(v);
  return out;
}

function Pending({ live }: { live: boolean }) {
  return (
    <Panel title="Whole program">
      <p className="px-2.5 py-2 text-ink-3">
        {live
          ? "When the search ends, oqed builds the project twice (plain and with the lock) and times both in alternating runs."
          : "No whole-program measurement in this run's log."}
      </p>
    </Panel>
  );
}

function FunctionTable({ run, tiles, onOpen }: { run: RunView; tiles: Tile[]; onOpen: (fn: string) => void }) {
  const share = new Map(tiles.map((t) => [t.fn.name, t]));
  const rows = [...run.functions].sort((a, b) => (share.get(b.id)?.fn.time ?? 0) - (share.get(a.id)?.fn.time ?? 0));
  return (
    <Panel title="Searched functions" right="hottest first" bodyClassName="overflow-x-auto">
      <table className="tabular w-full min-w-[36rem] border-collapse">
        <thead>
          <tr className="border-b border-rule-soft text-left text-ink-3">
            <th className="px-2.5 py-1 font-normal">function</th>
            <th className="px-2.5 py-1 text-right font-normal">samples</th>
            <th className="px-2.5 py-1 text-right font-normal">tried</th>
            <th className="w-[30%] px-2.5 py-1 font-normal">outcomes</th>
            <th className="px-2.5 py-1 text-right font-normal">result</th>
            <th className="px-2.5 py-1 text-right font-normal">spend</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((f) => {
            const t = share.get(f.id);
            const s = headlineSpeedup(f);
            const u = usage(f.candidates).total;
            return (
              <tr
                key={f.id}
                onClick={() => onOpen(f.id)}
                className="cursor-pointer border-b border-rule-soft text-ink-2 last:border-b-0 hover:bg-panel-head"
              >
                <td className="px-2.5 py-1 font-bold text-ink">{f.id}</td>
                <td className="px-2.5 py-1 text-right">{t ? pct(t.fn.time) : "—"}</td>
                <td className="px-2.5 py-1 text-right">{f.candidates.length}</td>
                <td className="px-2.5 py-1">
                  <Outcomes fn={f} />
                </td>
                <td className={`px-2.5 py-1 text-right font-bold ${s ? "text-good" : "text-ink-3"}`}>
                  {s ? `${s.toFixed(3)}×` : t?.state === "searching" ? "searching" : "no win"}
                </td>
                <td className="px-2.5 py-1 text-right">{fmtUsd(u.cost)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Panel>
  );
}

const STAGE_GROUP: { label: string; stages: RejectStage[]; cls: string }[] = [
  { label: "faster, kept", stages: [], cls: "bg-good" },
  { label: "correct, not faster", stages: ["benchmark"], cls: "bg-[var(--neutral-bar)]" },
  { label: "wrong or unsafe, caught", stages: ["test", "verify", "abi", "inline"], cls: "bg-bad" },
  { label: "didn't assemble", stages: ["assemble", "isa", "parse"], cls: "bg-warn" },
  { label: "repeat", stages: ["duplicate"], cls: "oqed-hatch" },
];

function Outcomes({ fn }: { fn: RunView["functions"][number] }) {
  const n = fn.candidates.length || 1;
  const counts = STAGE_GROUP.map((g) =>
    g.stages.length
      ? fn.candidates.filter((c) => c.rejected && g.stages.includes(c.rejected.stage)).length
      : fn.candidates.filter((c) => !c.rejected && c.status).length,
  );
  return (
    <div className="flex h-3 w-full gap-[2px]" title={STAGE_GROUP.map((g, i) => `${g.label}: ${counts[i]}`).join(" · ")}>
      {STAGE_GROUP.map((g, i) =>
        counts[i] ? <div key={g.label} className={`h-full ${g.cls}`} style={{ width: `${(counts[i] / n) * 100}%` }} /> : null,
      )}
    </div>
  );
}

const STAGE_TEXT: Partial<Record<RejectStage, string>> = {
  benchmark: "correct, but not faster on the project benchmark",
  test: "wrong: the project's test output or files differed",
  abi: "broke the calling convention (static check)",
  inline: "didn't build into the project",
  verify: "counterexample from the SMT proof",
  assemble: "didn't assemble",
  isa: "used instructions outside x86-64-v3",
  parse: "reply not in the expected format",
  duplicate: "repeated an earlier candidate",
};

function Caught({ run }: { run: RunView }) {
  const all = run.functions.flatMap((f) => f.candidates);
  const by = new Map<RejectStage, number>();
  for (const c of all) if (c.rejected) by.set(c.rejected.stage, (by.get(c.rejected.stage) ?? 0) + 1);
  const kept = all.filter((c) => !c.rejected && c.status).length;
  const rows = [
    { key: "kept", text: "passed everything: faster, same output", n: kept, good: true, bad: false },
    ...[...by]
      .sort((a, b) => b[1] - a[1])
      .map(([s, n]) => ({ key: s, text: STAGE_TEXT[s] ?? s, n, good: false, bad: CAUGHT.has(s) })),
  ];
  const max = Math.max(1, ...rows.map((r) => r.n));
  return (
    <Panel title="What happened to each candidate" right={`${all.length} candidates`}>
      <table className="tabular w-full">
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="border-b border-rule-soft text-ink-2 last:border-b-0">
              <td className="px-2.5 py-1">{r.text}</td>
              <td className="w-[32%] py-1">
                <div
                  className={`h-3 rounded-r-[3px] ${r.good ? "bg-good" : r.bad ? "bg-bad" : "bg-[var(--neutral-bar)]"}`}
                  style={{ width: `${(r.n / max) * 100}%`, minWidth: r.n ? 2 : 0 }}
                />
              </td>
              <td className="px-2.5 py-1 text-right font-bold text-ink">{r.n}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}
