// Serves run logs to the UI. Dev: every runs/*.jsonl and runs/demo/*.jsonl,
// live from disk. Build: only the curated runs/demo/ logs, emitted next to the
// app so the hosted site is fully static.
import fs from "node:fs";
import path from "node:path";
import type { Plugin } from "vite";

export interface RunIndexEntry {
  id: string;
  file: string;
  demo: boolean;
  fake: boolean;
  started: number;
  functions: string[];
  project: string | null; // whole-project runs
  result: ProjectResult | null; // a whole-project run's measured outcome
  wins: Win[]; // each function's best verified candidate
}

/** A function's best candidate in a run, for the cross-run leaderboard. */
export interface Win {
  function: string; // the run's function id (target name)
  location: string | null; // file:line in the project (project runs)
  speedup: number; // vs the faster -O3 build; project functions: the whole program, paired
  program: boolean; // measured on the whole program (the project's benchmark)
  status: string | null; // "proven" | "tested_only"
  model: string | null;
  tier: string | null;
  candidate: string;
}

export interface ProjectResult {
  speedup: number | null; // whole program, plain build / oqed-cc build
  valid: boolean | null;
  faster_runs: number | null;
  runs: number | null;
  unit: string | null;
  functions: number; // found by the scan
  searchable: number;
  won: string[]; // functions with a winner in this run
  applied: string[] | null; // functions the measured build substituted (the lock as measured)
  appliedAt: string[] | null; // the same as "path/of/file.c:function", the path as the build saw it
  cost: number | null;
}

function describe(file: string, demo: boolean): RunIndexEntry | null {
  const text = fs.readFileSync(file, "utf8");
  const [first, second] = text.split("\n", 2);
  try {
    const e = JSON.parse(first);
    let project: string | null = null;
    let result: ProjectResult | null = null;
    try {
      const s = JSON.parse(second);
      if (s.type === "project_scanned") {
        project = s.project;
        result = summarize(text, s);
      }
    } catch {
      /* a run with one event so far */
    }
    if (e.type !== "run_started") return null;
    const wins = e.config?.fake ? [] : bestOf(text, second);
    return {
      id: e.run_id,
      file: `runs/${path.basename(file)}`,
      demo,
      fake: Boolean(e.config?.fake),
      started: e.timestamp,
      functions: e.functions,
      project,
      result,
      wins,
    };
  } catch {
    return null;
  }
}

/** Each function's best candidate (its last new_best), with its speedup. */
function bestOf(text: string, second: string): Win[] {
  const locations = new Map<string, string>();
  try {
    const s = JSON.parse(second);
    if (s.type === "project_scanned") for (const f of s.functions) locations.set(f.name, f.location);
  } catch {
    /* no scan */
  }
  const program = new Set<string>(); // judged by the project's benchmark
  const proposed = new Map<string, { model: string; tier: string }>();
  const speedup = new Map<string, number>();
  const status = new Map<string, string>();
  const best = new Map<string, string>();
  for (const line of text.split("\n")) {
    if (!/"(baseline_ready|candidate_proposed|candidate_benchmarked|candidate_verified|new_best)"/.test(line)) continue;
    try {
      const e = JSON.parse(line);
      if (e.type === "baseline_ready") {
        if (e.unit != null || (e.clang?.cycles == null) !== (e.gcc?.cycles == null)) program.add(e.function_id);
      } else if (e.type === "candidate_proposed") proposed.set(e.candidate_id, { model: e.model, tier: e.model_tier });
      else if (e.type === "candidate_benchmarked")
        // Against the faster compiler (the smaller ratio); paired runs give one number.
        speedup.set(e.candidate_id, Math.min(e.speedup_vs_clang, e.speedup_vs_gcc));
      else if (e.type === "candidate_verified") status.set(e.candidate_id, e.status);
      else if (e.type === "new_best") best.set(e.function_id, e.candidate_id);
    } catch {
      /* a line being written */
    }
  }
  return [...best].flatMap(([fn, cid]) => {
    const s = speedup.get(cid);
    if (s == null) return [];
    const p = proposed.get(cid);
    return [
      {
        function: fn,
        location: locations.get(fn) ?? null,
        speedup: s,
        program: program.has(fn),
        status: status.get(cid) ?? null,
        model: p?.model ?? null,
        tier: p?.tier ?? null,
        candidate: cid,
      },
    ];
  });
}

function summarize(text: string, scanned: { functions: { skipped: string | null }[] }): ProjectResult {
  const r: ProjectResult = {
    speedup: null,
    valid: null,
    faster_runs: null,
    runs: null,
    unit: null,
    functions: scanned.functions.length,
    searchable: scanned.functions.filter((f) => !f.skipped).length,
    won: [],
    applied: null,
    appliedAt: null,
    cost: null,
  };
  const won = new Set<string>();
  for (const line of text.split("\n")) {
    // Only the few event types needed; skip parsing the bulky ones.
    if (!line.includes('"new_best"') && !line.includes('"run_finished"') && !line.includes('"project_measured"')) continue;
    try {
      const e = JSON.parse(line);
      if (e.type === "new_best") won.add(e.function_id);
      else if (e.type === "run_finished") r.cost = e.cost_usd ?? null;
      else if (e.type === "project_measured") {
        const m = e.report;
        r.speedup = m.speedup;
        r.valid = m.valid ?? (m.test_output_identical && m.output_files_identical);
        r.faster_runs = m.faster_runs ?? null;
        r.runs = m.plain?.runs?.length ?? null;
        r.unit = m.unit;
        // "file.c: name: substituted (...)" / "file.cc: name: UNSAFE: substituted ..."
        const hits = (m.notes ?? []).flatMap((n: string) => {
          const hit = /^(.*?): ([\w~]+): (?:UNSAFE: )?substituted/.exec(n);
          return hit ? [[hit[1], hit[2]]] : [];
        });
        r.applied = [...new Set<string>(hits.map((h: string[]) => h[1]))];
        r.appliedAt = [...new Set<string>(hits.map((h: string[]) => `${h[0]}:${h[1]}`))];
      }
    } catch {
      /* a line being written */
    }
  }
  r.won = [...won];
  return r;
}

function listRuns(runsDir: string, includeLocal: boolean): Map<string, { entry: RunIndexEntry; path: string }> {
  const out = new Map<string, { entry: RunIndexEntry; path: string }>();
  const dirs: [string, boolean][] = [[path.join(runsDir, "demo"), true]];
  if (includeLocal) dirs.push([runsDir, false]);
  for (const [dir, demo] of dirs) {
    if (!fs.existsSync(dir)) continue;
    for (const name of fs.readdirSync(dir)) {
      if (!name.endsWith(".jsonl") || out.has(name)) continue;
      const p = path.join(dir, name);
      const entry = describe(p, demo);
      if (entry) out.set(name, { entry, path: p });
    }
  }
  return out;
}

function index(runs: ReturnType<typeof listRuns>): string {
  const entries = [...runs.values()].map((r) => r.entry).sort((a, b) => b.started - a.started);
  return JSON.stringify(entries);
}

export function runsPlugin(runsDir: string): Plugin {
  return {
    name: "oqed-runs",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = req.url?.split("?")[0] ?? "";
        if (!url.startsWith("/runs/")) return next();
        const runs = listRuns(runsDir, true);
        if (url === "/runs/index.json") {
          res.setHeader("Content-Type", "application/json");
          res.end(index(runs));
          return;
        }
        const run = runs.get(path.basename(url));
        if (!run) return next();
        res.setHeader("Content-Type", "application/x-ndjson");
        fs.createReadStream(run.path).pipe(res);
      });
    },
    generateBundle() {
      const runs = listRuns(runsDir, false);
      this.emitFile({ type: "asset", fileName: "runs/index.json", source: index(runs) });
      for (const [name, run] of runs) {
        this.emitFile({ type: "asset", fileName: `runs/${name}`, source: fs.readFileSync(run.path, "utf8") });
      }
    },
  };
}
