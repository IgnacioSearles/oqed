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
    return {
      id: e.run_id,
      file: `runs/${path.basename(file)}`,
      demo,
      fake: Boolean(e.config?.fake),
      started: e.timestamp,
      functions: e.functions,
      project,
      result,
    };
  } catch {
    return null;
  }
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
