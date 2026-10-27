import { useState } from "react";
import type { FunctionView } from "../model";
import { modelLabel } from "../model";
import { Panel } from "./Panel";

const W = 720;
const H = 220;
const PAD = { l: 44, r: 92, t: 12, b: 28 };

/** A 1/2/5 × 10^k step giving about four gridlines up to `max`. */
function niceStep(max: number): number {
  const raw = max / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  return ([1, 2, 5, 10].find((m) => m * mag >= raw) ?? 10) * mag;
}

/** Latency of every measured candidate by proposal order, with the best-so-far
 * staircase and the compiler baselines. Axes are fixed from the full run so
 * nothing jumps during replay. Reasoning-tier candidates are diamonds. */
export function ProgressChart({ fn, full }: { fn: FunctionView; full: FunctionView }) {
  const [hover, setHover] = useState<string | null>(null);
  const nMax = Math.max(full.candidates.length, 1);
  const lat = [
    full.clang.cycles?.median ?? 0,
    full.gcc.cycles?.median ?? 0,
    ...full.candidates.map((c) => c.cycles?.median ?? 0),
  ];
  const step = niceStep(Math.max(...lat) * 1.1 || 1);
  const yMax = Math.ceil((Math.max(...lat) * 1.1 || 1) / step) * step;
  const x = (n: number) => PAD.l + ((n - 0.5) / nMax) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + (1 - v / yMax) * (H - PAD.t - PAD.b);

  const measured = fn.candidates.filter((c) => c.cycles);
  const steps = fn.bestHistory;
  const lastN = fn.candidates.length;
  let path = "";
  steps.forEach((s, i) => {
    const xEnd = i + 1 < steps.length ? steps[i + 1].n : Math.max(lastN, s.n);
    path += `${i === 0 ? "M" : "L"}${x(s.n)},${y(s.latency)} L${x(xEnd)},${y(s.latency)} `;
  });
  const ticks = Array.from({ length: Math.round(yMax / step) + 1 }, (_, i) => i * step);
  const hovered = measured.find((c) => c.id === hover);

  const baselines = [
    { key: "clang -O3", v: full.clang.cycles?.median },
    { key: "gcc -O3", v: full.gcc.cycles?.median },
  ].filter((b): b is { key: string; v: number } => b.v != null);
  // Keep the two baseline labels from overlapping when the values are close.
  const labelY = baselines.map((b) => y(b.v));
  if (labelY.length === 2 && Math.abs(labelY[0] - labelY[1]) < 12) {
    const mid = (labelY[0] + labelY[1]) / 2;
    const up = labelY[0] <= labelY[1] ? 0 : 1;
    labelY[up] = mid - 6;
    labelY[1 - up] = mid + 6;
  }

  return (
    <Panel title="Progress" right="latency per measured candidate · TSC ticks/call">
      <div className="relative overflow-x-auto px-1 py-1">
        <svg viewBox={`0 0 ${W} ${H}`} className="block w-full min-w-[560px]" role="img" aria-label="latency of candidates over the search">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} stroke="var(--rule-soft)" />
              <text x={PAD.l - 6} y={y(t) + 4} textAnchor="end" fontSize="11" fill="var(--ink-3)" className="tabular">
                {Number(t.toFixed(2))}
              </text>
            </g>
          ))}
          <text x={PAD.l} y={H - 8} fontSize="11" fill="var(--ink-3)">
            candidate 1
          </text>
          <text x={W - PAD.r} y={H - 8} fontSize="11" fill="var(--ink-3)" textAnchor="end">
            {nMax}
          </text>

          {fn.stalls.map((s, i) => (
            <g key={i}>
              <line
                x1={x(s.afterN + 0.5)}
                x2={x(s.afterN + 0.5)}
                y1={PAD.t}
                y2={H - PAD.b}
                stroke="var(--ink-3)"
                strokeDasharray="2 3"
              />
              <text x={x(s.afterN + 0.5) + 4} y={PAD.t + 10} fontSize="11" fill="var(--ink-3)">
                stall → reasoning
              </text>
            </g>
          ))}

          {baselines.map((b, i) => (
            <g key={b.key}>
              <line x1={PAD.l} x2={W - PAD.r} y1={y(b.v)} y2={y(b.v)} stroke="var(--ink-2)" strokeDasharray="5 4" />
              <text x={W - PAD.r + 6} y={labelY[i] + 4} fontSize="11" fill="var(--ink-2)">
                {b.key}
              </text>
            </g>
          ))}

          {path && <path d={path} fill="none" stroke="var(--accent)" strokeWidth="2" />}
          {steps.length > 0 && (
            <text
              x={W - PAD.r + 6}
              y={y(steps[steps.length - 1].latency) + 4}
              fontSize="11"
              fontWeight="700"
              fill="var(--accent)"
            >
              oqed best
            </text>
          )}

          {measured.map((c) => {
            const cx = x(c.n);
            const cy = y(c.cycles!.median);
            const won = !c.rejected;
            const fill = won ? "var(--accent)" : "var(--panel)";
            const stroke = won ? "var(--accent)" : "var(--ink-3)";
            const r = hover === c.id ? 6 : 4.5;
            return (
              <g key={c.id} onMouseEnter={() => setHover(c.id)} onMouseLeave={() => setHover(null)}>
                <circle cx={cx} cy={cy} r={12} fill="transparent" />
                {c.tier === "reasoning" ? (
                  <rect
                    x={cx - r}
                    y={cy - r}
                    width={r * 2}
                    height={r * 2}
                    transform={`rotate(45 ${cx} ${cy})`}
                    fill={fill}
                    stroke={stroke}
                    strokeWidth="1.5"
                  />
                ) : (
                  <circle cx={cx} cy={cy} r={r} fill={fill} stroke={stroke} strokeWidth="1.5" />
                )}
              </g>
            );
          })}
        </svg>
        {hovered && (
          <div
            role="tooltip"
            className="pointer-events-none absolute z-10 border border-rule bg-panel px-2 py-1 whitespace-nowrap"
            style={{
              left: `min(${(x(hovered.n) / W) * 100}%, calc(100% - 20rem))`,
              top: `${(y(hovered.cycles!.median) / H) * 100}%`,
              transform: "translate(8px, -120%)",
            }}
          >
            <div className="font-bold text-ink">
              #{hovered.n} · {hovered.cycles!.median.toFixed(2)} ticks
            </div>
            <div className="text-ink-3">
              {modelLabel(hovered.model)} ({hovered.tier}) ·{" "}
              {hovered.rejected ? `${hovered.rejected.stage}: rejected` : (hovered.status ?? "").replace("_", " ")}
            </div>
          </div>
        )}
      </div>
      <div className="flex flex-wrap gap-x-4 border-t border-rule-soft px-2.5 py-1.5 text-ink-3">
        <span>
          <span className="text-accent">●</span> kept
        </span>
        <span>○ measured, not faster</span>
        <span>◇ reasoning tier</span>
        <span>
          <span className="text-accent">━</span> best so far
        </span>
        <span>╌ compiler -O3</span>
      </div>
    </Panel>
  );
}
