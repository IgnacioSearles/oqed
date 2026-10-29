// A whole-project run as the codebase map shows it: every function the scan
// found, with what the run did to it so far.

import type { ScannedFunction } from "./events";
import { type FunctionView, type RunView, headlineSpeedup } from "./model";

export type TileState =
  | "won" // a faster version is in the lock (this run or an earlier one)
  | "searching" // the search is running now
  | "nowin" // searched, nothing beat the compiler
  | "queued" // chosen for this run, not started yet
  | "idle" // oqed could search it, this run didn't
  | "skipped"; // oqed can't search it (too big, unsupported signature, main)

export interface Tile {
  fn: ScannedFunction;
  file: string;
  state: TileState;
  speedup: number | null;
  earlier: boolean; // won in an earlier run
  view: FunctionView | null;
  tried: number; // candidates proposed so far
  lastCaught: number | null; // when a wrong or ABI-breaking candidate was last rejected
}

/** A function's area on the map: its own code's share when the log has it
 * (shares including inlined callees overlap and can add up past 100%). */
export const area = (fn: ScannedFunction) => fn.own ?? fn.time;

/** Rejections that mean the checks caught code that would have been wrong. */
export const CAUGHT = new Set(["test", "verify", "abi", "inline"]);

export function tiles(run: RunView): Tile[] {
  const p = run.project;
  if (!p) return [];
  const views = new Map(run.functions.map((f) => [f.id, f]));
  const earlier = new Map(p.locked.filter((e) => e.run !== run.id).map((e) => [e.function, e]));
  return p.functions.map((fn) => {
    const view = views.get(fn.name) ?? null;
    const file = fn.location.replace(/:\d+$/, "");
    const caught = view?.candidates.filter((c) => c.rejected && CAUGHT.has(c.rejected.stage)) ?? [];
    const base = {
      fn,
      file,
      view,
      speedup: null as number | null,
      earlier: false,
      tried: view?.candidates.length ?? 0,
      lastCaught: caught.length ? Math.max(...caught.map((c) => c.rejected!.at)) : null,
    };
    const lockedBefore = earlier.get(fn.function);
    if (view?.best) {
      const done = Boolean(view.stopped) || run.finished;
      return { ...base, state: done ? "won" : "searching", speedup: headlineSpeedup(view) };
    }
    if (view) return { ...base, state: view.stopped || run.finished ? "nowin" : "searching" };
    if (lockedBefore) return { ...base, state: "won", speedup: lockedBefore.speedup, earlier: true };
    if (fn.searched) return { ...base, state: run.finished ? "nowin" : "queued" };
    return { ...base, state: fn.skipped ? "skipped" : "idle" };
  });
}

export interface TimeSplit {
  won: number;
  nowin: number; // searched without a win
  idle: number; // searchable, not searched
  skipped: number; // project code oqed can't search
  outside: number; // libc and everything else outside the project
}

export function timeSplit(ts: Tile[]): TimeSplit {
  const s: TimeSplit = { won: 0, nowin: 0, idle: 0, skipped: 0, outside: 0 };
  for (const t of ts) {
    const k = t.state === "won" ? "won" : t.state === "skipped" ? "skipped" : t.state === "idle" ? "idle" : "nowin";
    s[k] += area(t.fn);
  }
  s.outside = Math.max(0, 1 - s.won - s.nowin - s.idle - s.skipped);
  return s;
}

export const pct = (x: number) => (x >= 0.1 ? `${(100 * x).toFixed(0)}%` : x >= 0.001 ? `${(100 * x).toFixed(1)}%` : "<0.1%");
