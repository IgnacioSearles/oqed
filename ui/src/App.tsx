import { useCallback, useEffect, useMemo, useState } from "react";
import { Leaderboard, RunList } from "./components/Leaderboard";
import { Panel } from "./components/Panel";
import { ResultView } from "./components/ResultView";
import { SearchView } from "./components/SearchView";
import { type RunEvent, type RunIndexEntry, parseJsonl } from "./events";
import { buildRun, modelLabel } from "./model";
import { countUntil, useReplay } from "./replay";

type Theme = "light" | "dark";
type View = "result" | "search";

const POLL_MS = 1500;

function readHash(): { run?: string; fn?: string; view: View } {
  const p = new URLSearchParams(location.hash.slice(1));
  return {
    run: p.get("run") ?? undefined,
    fn: p.get("fn") ?? undefined,
    view: p.get("view") === "search" ? "search" : "result",
  };
}

function writeHash(run: string | undefined, fn: string | undefined, view: View) {
  const p = new URLSearchParams();
  if (run) p.set("run", run);
  if (fn) p.set("fn", fn);
  if (view !== "result") p.set("view", view);
  history.replaceState(null, "", `#${p}`);
}

export default function App() {
  const [runs, setRuns] = useState<RunIndexEntry[]>([]);
  const [runId, setRunId] = useState<string | undefined>(readHash().run);
  const [fnId, setFnId] = useState<string | undefined>(readHash().fn);
  const [view, setView] = useState<View>(readHash().view);
  const [events, setEvents] = useState<RunEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [theme, setTheme] = useState<Theme>(() => (document.documentElement.dataset.theme as Theme) ?? "light");

  useEffect(() => {
    fetch("runs/index.json")
      .then((r) => r.json())
      .then((list: RunIndexEntry[]) => {
        setRuns(list);
        // Default to the newest real (non-synthetic) run, preferring demo runs.
        setRunId((cur) => cur ?? (list.find((r) => r.demo && !r.fake) ?? list.find((r) => !r.fake) ?? list[0])?.id);
      })
      .catch(() => setError("could not load the run index"));
  }, []);

  const entry = runs.find((r) => r.id === runId);
  useEffect(() => {
    if (!entry) return;
    setEvents(null);
    fetch(entry.file)
      .then((r) => r.text())
      .then((t) => setEvents(parseJsonl(t)))
      .catch(() => setError(`could not load run ${entry.id}`));
  }, [entry]);

  const run = useMemo(() => (events ? buildRun(events) : null), [events]);
  const fn = run?.functions.find((f) => f.id === fnId) ?? run?.functions[0];
  // Local runs still in progress: re-read the log until it finishes.
  const live = Boolean(entry && run && !run.finished && !entry.demo);

  useEffect(() => {
    if (!live || !entry) return;
    const id = setInterval(() => {
      fetch(entry.file, { cache: "no-store" })
        .then((r) => r.text())
        .then((t) => setEvents(parseJsonl(t)))
        .catch(() => {});
    }, POLL_MS);
    return () => clearInterval(id);
  }, [live, entry]);

  useEffect(() => writeHash(runId, fn?.id, view), [runId, fn?.id, view]);

  // Replay covers the selected function's part of the run.
  const timestamps = useMemo(() => (events ?? []).map((e) => e.timestamp), [events]);
  const range = useMemo(() => {
    let start = Infinity;
    let end = 0;
    for (const e of events ?? []) {
      if ("function_id" in e && e.function_id === fn?.id) {
        start = Math.min(start, e.timestamp);
        end = Math.max(end, e.timestamp);
      }
    }
    return start <= end ? { start, end } : { start: 0, end: 0 };
  }, [events, fn?.id]);
  const replay = useReplay(range.start, range.end);
  const shown = useMemo(() => {
    if (!events || !fn || replay.atEnd) return fn;
    const partial = buildRun(events.slice(0, countUntil(timestamps, replay.cursor)));
    return partial.functions.find((f) => f.id === fn.id) ?? fn;
  }, [events, timestamps, fn, replay.cursor, replay.atEnd]);

  const toggleTheme = useCallback(() => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("oqed-theme", next);
    } catch {
      /* storage may be unavailable; the toggle still works for this visit */
    }
    setTheme(next);
  }, [theme]);

  const models = run
    ? [run.models.volume, run.models.reasoning]
        .filter((m): m is string => Boolean(m))
        .map(modelLabel)
        .join(" + ")
    : "";

  return (
    <div className="mx-auto max-w-[1600px] space-y-1.5 p-1.5 sm:p-2">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-1 border border-rule bg-panel px-2.5 py-1.5">
        <span className="text-base font-bold">oqed</span>
        <span className="text-ink-3">faster than -O3. proven equivalent.</span>
        <span className="ml-auto flex items-center gap-3 text-ink-3">
          {run && <span className="hidden truncate md:inline">run {run.id}</span>}
          <span className="flex" role="group" aria-label="view">
            {(["result", "search"] as const).map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                aria-pressed={view === v}
                className={`-ml-px border border-rule px-2 leading-6 first:ml-0 ${
                  view === v ? "bg-select font-bold text-ink" : "bg-panel text-ink-3 hover:text-ink"
                }`}
              >
                {v}
              </button>
            ))}
          </span>
          <button
            onClick={toggleTheme}
            className="border border-rule bg-panel-head px-2 leading-6 text-ink-2 hover:text-ink"
            aria-label={`switch to ${theme === "dark" ? "light" : "dark"} mode`}
          >
            {theme === "dark" ? "light" : "dark"}
          </button>
        </span>
      </header>

      {error && <div className="border border-bad bg-panel px-2.5 py-1.5 text-bad">error: {error}</div>}

      <div className="grid gap-1.5 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <aside className="space-y-1.5 lg:sticky lg:top-2 lg:self-start">
          <RunList
            runs={runs}
            selected={runId}
            onSelect={(id) => {
              setRunId(id);
              setFnId(undefined);
            }}
          />
          {run && <Leaderboard functions={run.functions} selected={fn?.id ?? null} onSelect={setFnId} />}
        </aside>

        <main className="min-w-0 space-y-1.5">
          {run?.fake && (
            <div className="border border-warn bg-warn-bg px-2.5 py-1.5 text-warn">
              synthetic run for UI development: baselines are real, search results are made up
            </div>
          )}
          {!run && !error && <div className="border border-rule bg-panel px-2.5 py-1.5 text-ink-3">loading…</div>}
          {fn && view === "result" && <ResultView fn={fn} />}
          {fn && shown && view === "search" && (
            <SearchView fn={shown} full={fn} replay={replay} live={live && replay.atEnd} />
          )}
          {run && (
            <Panel title="Method">
              <p className="max-w-5xl px-2.5 py-2 leading-relaxed text-ink-3">
                Candidates generated by {models || "Muse models"}. Latency is the median TSC ticks per call over 101
                repetitions of 4,096 calls cycling through 1,024 mixed inputs, with the interquartile range, call
                overhead included, measured on a development machine. “Proven equivalent” means symbolic execution
                (angr) and Z3 show the code returns the same value and preserves the same registers as the compiler’s
                output for every input the System V ABI allows. “Tested” code passed differential testing but could
                not be proven.
              </p>
            </Panel>
          )}
        </main>
      </div>
    </div>
  );
}
