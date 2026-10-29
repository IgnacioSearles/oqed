import { useCallback, useEffect, useMemo, useState } from "react";
import { Leaderboard, RunList } from "./components/Leaderboard";
import { Panel } from "./components/Panel";
import { ProjectView } from "./components/ProjectView";
import { ProjectsTable } from "./components/ProjectsTable";
import { ResultView } from "./components/ResultView";
import { SearchView } from "./components/SearchView";
import { WinsBoard } from "./components/WinsBoard";
import { type RunEvent, type RunIndexEntry, parseJsonl } from "./events";
import { buildRun, fmtUsd, modelLabel, usage } from "./model";
import { countUntil, useReplay } from "./replay";

type Theme = "light" | "dark";
type View = "projects" | "leaderboard" | "project" | "result" | "search";
// Views of all runs, not of the selected one.
const GLOBAL: View[] = ["projects", "leaderboard"];

const POLL_MS = 1500;
const INDEX_POLL_MS = 3000;
// A project run's whole-program measurement comes after run_finished; stop
// waiting for it once the log has been quiet this long.
const MEASURE_WAIT_S = 20 * 60;

function readHash(): { run?: string; fn?: string; view?: View } {
  const p = new URLSearchParams(location.hash.slice(1));
  const v = p.get("view");
  return {
    run: p.get("run") ?? undefined,
    fn: p.get("fn") ?? undefined,
    view: v === "search" || v === "result" || v === "project" || v === "projects" || v === "leaderboard" ? v : undefined,
  };
}

function writeHash(run: string | undefined, fn: string | undefined, view: View) {
  const p = new URLSearchParams();
  if (run && !GLOBAL.includes(view)) p.set("run", run);
  if (fn && view !== "project" && !GLOBAL.includes(view)) p.set("fn", fn);
  p.set("view", view);
  history.replaceState(null, "", `#${p}`);
}

export default function App() {
  const [runs, setRuns] = useState<RunIndexEntry[]>([]);
  const [runId, setRunId] = useState<string | undefined>(readHash().run);
  const [fnId, setFnId] = useState<string | undefined>(readHash().fn);
  // A plain visit opens on the projects table; a link to a run opens that run.
  const [chosenView, setView] = useState<View | undefined>(() => {
    const h = readHash();
    return h.view ?? (h.run ? undefined : "projects");
  });
  const [events, setEvents] = useState<RunEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [theme, setTheme] = useState<Theme>(() => (document.documentElement.dataset.theme as Theme) ?? "light");

  // The run list: re-read while developing, so new runs show up without a reload.
  useEffect(() => {
    const load = (first: boolean) =>
      fetch("runs/index.json", { cache: "no-store" })
        .then((r) => r.json())
        .then((list: RunIndexEntry[]) => {
          setRuns((cur) => (JSON.stringify(cur) === JSON.stringify(list) ? cur : list));
          // Default to the newest real (non-synthetic) run, preferring demo runs.
          setRunId((cur) => cur ?? (list.find((r) => r.demo && !r.fake) ?? list.find((r) => !r.fake) ?? list[0])?.id);
        })
        .catch(() => first && setError("could not load the run index"));
    load(true);
    if (!import.meta.env.DEV) return;
    const id = setInterval(() => load(false), INDEX_POLL_MS);
    return () => clearInterval(id);
  }, []);

  const entry = useMemo(() => runs.find((r) => r.id === runId), [runs, runId]);
  useEffect(() => {
    if (!entry) return;
    setEvents(null);
    fetch(entry.file)
      .then((r) => r.text())
      .then((t) => setEvents(parseJsonl(t)))
      .catch(() => setError(`could not load run ${entry.id}`));
  }, [entry]);

  const run = useMemo(() => (events ? buildRun(events) : null), [events]);
  const timestamps = useMemo(() => (events ?? []).map((e) => e.timestamp), [events]);
  const runCost = useMemo(() => (run ? usage(run.functions.flatMap((f) => f.candidates)).total.cost : null), [run]);
  const fn = run?.functions.find((f) => f.id === fnId) ?? run?.functions[0];
  const view: View = chosenView ?? (run?.project ? "project" : "result");
  const lastTs = events?.length ? events[events.length - 1].timestamp : 0;
  // Local runs still in progress: re-read the log until it finishes (for a
  // project run, until the whole program has been measured).
  const live = Boolean(
    entry &&
      run &&
      !entry.demo &&
      (!run.finished || (run.project && !run.project.report && Date.now() / 1000 - lastTs < MEASURE_WAIT_S)),
  );

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

  // The project view replays the whole run.
  const runRange = useMemo(
    () => ({ start: events?.[0]?.timestamp ?? 0, end: events?.length ? events[events.length - 1].timestamp : 0 }),
    [events],
  );
  const runReplay = useReplay(runRange.start, runRange.end);
  const runShown = useMemo(() => {
    if (!events || !run || runReplay.atEnd) return run;
    return buildRun(events.slice(0, countUntil(timestamps, runReplay.cursor)));
  }, [events, run, timestamps, runReplay.cursor, runReplay.atEnd]);

  // Replay covers the selected function's part of the run.
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
          {run && (
            <span className="hidden truncate md:inline">
              run {run.id}
              {runCost != null && runCost > 0 && ` · ${fmtUsd(runCost)}`}
            </span>
          )}
          <span className="flex" role="group" aria-label="view">
            {(run?.project
              ? (["projects", "leaderboard", "project", "result", "search"] as const)
              : (["projects", "leaderboard", "result", "search"] as const)
            ).map((v) => (
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
              setView(undefined);
            }}
          />
          {run && !GLOBAL.includes(view) && (
            <Leaderboard
              functions={run.functions}
              selected={view === "project" ? null : (fn?.id ?? null)}
              onSelect={(id) => {
                setFnId(id);
                if (view === "project") setView("result");
              }}
            />
          )}
        </aside>

        <main className="min-w-0 space-y-1.5">
          {run?.fake && (
            <div className="border border-warn bg-warn-bg px-2.5 py-1.5 text-warn">
              synthetic run for UI development: baselines are real, search results are made up
            </div>
          )}
          {!run && !error && <div className="border border-rule bg-panel px-2.5 py-1.5 text-ink-3">loading…</div>}
          {view === "leaderboard" && (
            <WinsBoard
              runs={runs}
              onOpen={(id, fnName) => {
                setRunId(id);
                setFnId(fnName);
                setView("result");
              }}
            />
          )}
          {view === "projects" && (
            <ProjectsTable
              runs={runs}
              onOpen={(id) => {
                setRunId(id);
                setFnId(undefined);
                setView("project");
              }}
            />
          )}
          {run && runShown && view === "project" && (
            <ProjectView
              run={runShown}
              full={run}
              replay={runReplay}
              live={live && runReplay.atEnd}
              onOpen={(id) => {
                setFnId(id);
                setView("result");
              }}
            />
          )}
          {fn && view === "result" && <ResultView fn={fn} unit={run?.project?.report?.unit} />}
          {fn && shown && view === "search" && (
            <SearchView fn={shown} full={fn} replay={replay} live={live && replay.atEnd} />
          )}
          {run?.project && view === "project" && (
            <Panel title="Method">
              <p className="max-w-5xl px-2.5 py-2 leading-relaxed text-ink-3">
                oqed builds the project once with a sampling profiler (100 µs timer, every thread) and runs its workload;
                each function's tile is its share of the samples, inlined callees included. It searches the hottest
                functions it can rewrite. A candidate is substituted into a copy of the project, built, and kept only if the
                project's test prints the same output and writes byte-identical files, and the benchmark is faster in a
                paired sign test (p ≤ 0.07) run with nothing else benchmarking. Candidates that call other functions must
                pass a static System V ABI check first. Candidates generated by {models || "Muse models"}. The whole-program
                number compares the plain build with the build through oqed-cc in alternating runs, measured on a development
                laptop. “Tested” code passed these checks but has no SMT proof.
              </p>
            </Panel>
          )}
          {run && !run.project && !GLOBAL.includes(view) && (
            <Panel title="Method">
              <p className="max-w-5xl px-2.5 py-2 leading-relaxed text-ink-3">
                Candidates generated by {models || "Muse models"}.{" "}
                {run.config.bench === "inline"
                  ? "Latency is measured with the code inlined as an asm block into a loop where each result feeds the next call, against the C compiled into the same loop by clang and gcc: median TSC ticks per iteration over 101 samples of 8 passes through 1,024 mixed inputs, with the interquartile range, measured on a development machine."
                  : "Latency is the median TSC ticks per call over 101 repetitions of 4,096 calls cycling through 1,024 mixed inputs, with the interquartile range, call overhead included, measured on a development machine."} “Proven equivalent” means symbolic execution
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
