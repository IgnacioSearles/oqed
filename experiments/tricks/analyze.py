"""Results of the trick-library experiment (run.sh): per function and arm,
whether it was won, the best speedup, candidates until the first win, and
spend; then totals per arm.

    uv run python experiments/tricks/analyze.py   # prints a markdown table
"""

import json
import statistics
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"  # copies of the four runs (runs/ is not committed)


def per_function(run_id: str) -> dict[str, dict]:
    events = [json.loads(ln) for ln in (RUNS / f"{run_id}.jsonl").read_text().splitlines() if ln.strip()]
    out: dict[str, dict] = {}
    n: dict[str, int] = defaultdict(int)
    order: dict[str, int] = {}
    bench = {}
    for e in events:
        fid = e.get("function_id")
        if e["type"] == "baseline_ready":
            out[fid] = {"won": False, "first": None, "best": None, "candidates": 0, "cost": 0.0, "status": None}
        elif e["type"] == "candidate_proposed":
            n[fid] += 1
            order[e["candidate_id"]] = n[fid]
            out[fid]["candidates"] += 1
            out[fid]["cost"] += e.get("cost_usd") or 0.0
        elif e["type"] == "candidate_benchmarked":
            bench[e["candidate_id"]] = min(e["speedup_vs_clang"], e["speedup_vs_gcc"])
        elif e["type"] == "candidate_verified":
            out[fid].setdefault("verified", {})[e["candidate_id"]] = e["status"]
        elif e["type"] == "new_best":
            f = out[fid]
            f["won"] = True
            f["first"] = f["first"] or order[e["candidate_id"]]
            f["best"] = bench.get(e["candidate_id"])
            f["status"] = f.get("verified", {}).get(e["candidate_id"])
    return out


def main() -> None:
    rows = [ln.split() for ln in (HERE / "runs.txt").read_text().splitlines() if ln.strip()]
    results: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))  # arm -> fn -> per rep
    for k, _rep, run_id in rows:
        for fid, r in per_function(run_id).items():
            results[k][fid].append(r)
    arms = sorted(results)
    fns = sorted({f for a in arms for f in results[a]})
    print("| function | " + " | ".join(f"tricks={a}: won, best, first win at" for a in arms) + " |")
    print("|---|" + "---|" * len(arms))
    for f in fns:
        cells = []
        for a in arms:
            reps = results[a][f]
            won = sum(r["won"] for r in reps)
            best = max((r["best"] or 0 for r in reps), default=0)
            firsts = [r["first"] for r in reps if r["first"]]
            cells.append(
                f"{won}/{len(reps)}, {best:.2f}x, {'/'.join(map(str, firsts)) or '-'}" if won else f"0/{len(reps)}"
            )
        print(f"| {f} | " + " | ".join(cells) + " |")
    print()
    for a in arms:
        reps = [r for f in results[a].values() for r in f]
        wins = [r for r in reps if r["won"]]
        cands = sum(r["candidates"] for r in reps)
        cost = sum(r["cost"] for r in reps)
        bests = [r["best"] for r in wins if r["best"]]
        print(
            f"tricks={a}: {len(wins)} of {len(reps)} function searches won "
            f"({sum(r['status'] == 'proven' for r in wins)} proven), {cands} candidates "
            f"({cands / max(len(wins), 1):.1f} per win), first win at candidate "
            f"{statistics.median([r['first'] for r in wins]) if wins else '-'} (median), "
            f"best speedup median {statistics.median(bests) if bests else 0:.2f}x, "
            f"${cost:.2f} (${cost / max(len(wins), 1):.3f} per win)"
        )


if __name__ == "__main__":
    main()
