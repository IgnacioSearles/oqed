"""oqed command-line interface."""

from __future__ import annotations

from pathlib import Path

import typer

from . import baseline, fake, harness
from .isa import forbidden_instructions, isa_violations
from .targets import load_all, load_target
from .toolchain import DEFAULT_MARCH, DEFAULT_MCPU, ToolError, assemble, compile_to_asm

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command("baseline")
def baseline_cmd(name: str, march: str = DEFAULT_MARCH, mcpu: str = DEFAULT_MCPU, report: bool = False):
    """Show clang and gcc -O3 output and llvm-mca estimates for one target."""
    t = load_target(name)
    for compiler in ("clang", "gcc"):
        b = baseline.compute(t, compiler, march, mcpu)
        typer.secho(
            f"== {compiler} -O3 -march={march}  (mca {mcpu}: "
            f"{b.mca.instructions} insns, {b.mca.uops} uops, "
            f"tput {b.mca.rthroughput}, lat {b.mca.latency})",
            bold=True,
        )
        typer.echo(b.asm)
        if report:
            typer.echo(b.mca.report)


@app.command()
def scout(march: str = DEFAULT_MARCH, mcpu: list[str] = typer.Option([DEFAULT_MCPU])):
    """Markdown table of compiler baselines across all targets."""
    cols = " | ".join(f"{c} {m} lat/tput" for m in mcpu for c in ("clang", "gcc"))
    typer.echo(f"| target | clang insns | gcc insns | {cols} |")
    typer.echo("|---" * (3 + 2 * len(mcpu)) + "|")
    for t in load_all():
        base = {c: baseline.compute(t, c, march, mcpu[0]) for c in ("clang", "gcc")}
        cells = []
        for m in mcpu:
            for c in ("clang", "gcc"):
                r = base[c].mca if m == mcpu[0] else baseline.compute(t, c, march, m).mca
                cells.append(f"{r.latency}/{r.rthroughput}")
        typer.echo(
            f"| {t.name} | {base['clang'].mca.instructions} | {base['gcc'].mca.instructions} | "
            + " | ".join(cells)
            + " |"
        )


@app.command("fake-run")
def fake_run(
    targets: list[str] = typer.Argument(None), candidates: int = 40, seed: int = 0, delay: float = 0.0
):
    """Write a synthetic run (real baselines, fake search) for UI development."""
    ts = [load_target(n) for n in targets] if targets else load_all()
    typer.echo(fake.generate(ts, candidates, seed, delay))


@app.command("try")
def try_cmd(name: str, asm_file: Path, march: str = DEFAULT_MARCH):
    """Check a hand-written candidate (Intel-syntax function body) against a target."""
    t = load_target(name)
    try:
        code = assemble(asm_file.read_text())
    except ToolError as e:
        typer.secho(f"assemble: {e}", fg="red")
        raise typer.Exit(1) from None
    if bad := forbidden_instructions(code) or isa_violations(code, march):
        typer.secho(f"rejected: {', '.join(bad)}", fg="red")
        raise typer.Exit(1)
    ref = {c: assemble(compile_to_asm(t, c, march)) for c in ("clang", "gcc")}
    res = harness.difftest(t, ref["clang"], code)
    if not res.passed:
        typer.secho(f"test failed: {res.reason}", fg="red")
        if res.counterexample:
            typer.echo(f"  counterexample: {res.counterexample.model_dump_json()}")
        raise typer.Exit(1)
    typer.secho(f"passed {res.inputs_tested} differential tests vs clang", fg="green")
    typer.echo("TSC ticks/call (median ± IQR), call overhead included:")
    for label, c in [("clang", ref["clang"]), ("gcc", ref["gcc"]), ("candidate", code)]:
        lat, tput = harness.measure(c, t, latency=True), harness.measure(c, t, latency=False)
        typer.echo(
            f"  {label:9}  latency {lat.median:6.2f} ± {lat.spread:.2f}"
            f"   throughput {tput.median:6.2f} ± {tput.spread:.2f}"
        )


def _print_event(e) -> None:
    fn = getattr(e, "function_id", "") or ""
    match e.type:
        case "baseline_ready":
            # The project oracle times only the project's own compiler.
            pairs = (("clang", e.clang), ("gcc", e.gcc))
            timed = [f"{c} {b.cycles.median:.2f}" for c, b in pairs if b.cycles]
            typer.secho(f"[{fn}] baseline: {', '.join(timed)}", bold=True)
        case "candidate_proposed":
            tier = "R" if e.model_tier == "reasoning" else "v"
            typer.echo(
                f"[{fn}] {e.candidate_id} {tier} {e.model} ({(e.model_ms or 0) / 1000:.1f}s): {e.explanation}"
            )
        case "candidate_rejected":
            ce = f" {e.counterexample}" if e.counterexample else ""
            typer.secho(f"[{fn}]   x {e.stage}: {e.reason}{ce}", fg="red")
        case "candidate_benchmarked":
            typer.echo(
                f"[{fn}]   {e.cycles.median:.2f} ± {e.cycles.spread:.2f} ticks "
                f"(x{e.speedup_vs_clang} vs clang, x{e.speedup_vs_gcc} vs gcc)"
            )
        case "candidate_verified":
            if e.status == "proven":
                typer.secho(f"[{fn}]   proven equivalent", fg="green")
            else:
                typer.secho(f"[{fn}]   tested only: {e.reason}", fg="yellow")
        case "new_best":
            typer.secho(f"[{fn}]   * new best: {e.candidate_id}", fg="green", bold=True)
        case "search_stalled":
            typer.secho(
                f"[{fn}] stalled for {e.rounds_without_improvement}; calling reasoning tier", fg="yellow"
            )
        case "search_stopped":
            typer.secho(f"[{fn}] stopped: {e.reason}", fg="yellow")
        case "run_finished":
            for name, s in e.summary.items():
                sp = (
                    f"x{s.speedup_vs_clang} vs clang, x{s.speedup_vs_gcc} vs gcc"
                    if s.best_candidate_id
                    else "no win"
                )
                typer.secho(f"{name}: {sp} ({s.status or '-'}, {s.candidates} candidates)", bold=True)
            if e.cost_usd is not None:
                typer.secho(f"model spend: ${e.cost_usd:.2f} (list-price estimate)", bold=True)


@app.command()
def search(
    targets: list[str] = typer.Argument(..., help="benchmark names"),
    volume: str = typer.Option(
        "spark-contributor", help="volume-tier model (see oqed.providers.MODELS); `spark` for demo runs"
    ),
    reasoning: str = typer.Option("wonder", help="reasoning-tier model"),
    candidates: int = 24,
    concurrency: int = 8,
    stall_after: int = 8,
    reasoning_attempts: int = 2,
    max_usd: float = typer.Option(1.0, help="stop the run once its model spend reaches this (USD)"),
    volume_effort: str = "low",
    reasoning_effort: str = "high",
    bench: str = typer.Option("inline", help="latency measurement: `inline` (asm in a loop) or `call`"),
):
    """Run the model-guided search on one or more targets."""
    import asyncio

    from .providers import get_provider
    from .search import SearchConfig, run_search

    cfg = SearchConfig(
        candidates=candidates,
        concurrency=concurrency,
        stall_after=stall_after,
        reasoning_attempts=reasoning_attempts,
        max_usd=max_usd,
        volume_effort=volume_effort,
        reasoning_effort=reasoning_effort,
        bench=bench,
    )
    run_id = asyncio.run(
        run_search(
            [load_target(n) for n in targets],
            get_provider(volume),
            get_provider(reasoning),
            cfg,
            on_event=_print_event,
        )
    )
    typer.echo(f"run log: runs/{run_id}.jsonl")


@app.command("inline-bench")
def inline_bench(
    run: str = typer.Argument(..., help="run id or path to runs/<id>.jsonl"),
    function: str = typer.Option(None, help="only this function"),
    every_winner: bool = typer.Option(False, help="every candidate that beat -O3, not just each new best"),
    march: str = DEFAULT_MARCH,
):
    """Re-measure a run's winners inlined into loops, and re-prove the compiled bytes."""
    import json

    from . import inline

    path = Path(run) if run.endswith(".jsonl") else Path("runs") / f"{run}.jsonl"
    events = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
    asm = {e["candidate_id"]: e["asm"] for e in events if e["type"] == "candidate_proposed"}
    call_mode = {
        e["candidate_id"]: min(e["speedup_vs_clang"], e["speedup_vs_gcc"])
        for e in events
        if e["type"] == "candidate_benchmarked"
    }
    status = {e["candidate_id"]: e["status"] for e in events if e["type"] == "candidate_verified"}
    kind = "candidate_verified" if every_winner else "new_best"
    picked = [(e["function_id"], e["candidate_id"]) for e in events if e["type"] == kind]
    picked = [p for p in picked if function in (None, p[0])]
    if not picked:
        typer.echo("no winners in this run")
        raise typer.Exit(1)

    typer.echo("speedup vs the better of clang/gcc -O3, laptop TSC ticks per element")
    typer.echo(
        f"{'candidate':28} {'call':>6} {'latency':>8} {'tput':>6} {'tput C scalar':>14}  "
        "C vectorized    proof (call)   inlined clang / gcc"
    )
    for fid, cid in picked:
        t = load_target(fid)
        try:
            res = {c: inline.measure(asm[cid], t, c, march) for c in ("clang", "gcc")}
        except (inline.NotInlinable, ToolError) as e:
            typer.secho(f"{cid:28} {call_mode.get(cid, 0):5.2f}x  not inlinable: {e}", fg="yellow")
            continue
        proofs = [inline.reverify(asm[cid], t, c, march).status for c in ("clang", "gcc")]

        best = {k: min(getattr(r, k).median for r in res.values()) for k in inline.VARIANTS}
        vec = [c for c, r in res.items() if r.ref_vectorized]
        lat = best["dep_ref"] / best["dep_cand"]
        tput, scalar = best["indep_ref"] / best["indep_cand"], best["scalar_ref"] / best["indep_cand"]
        typer.echo(
            f"{cid:28} {call_mode.get(cid, 0):5.2f}x {lat:7.2f}x {tput:5.2f}x {scalar:13.2f}x  "
            f"{', '.join(vec) or 'no':14}  {status.get(cid, '?'):13}  {' / '.join(proofs)}"
        )


def _print_scan(sc) -> None:
    targets = sc.targets
    n = len(sc.functions)
    typer.echo(f"{n} functions in the project, {len(targets)} oqed can search (most-called first):")
    typer.echo(f"  {'calls':>12}  function{'':24} location")
    for f in targets:
        typer.echo(f"  {f.calls:>12,}  {f.name:<32} {f.location}")
    hot = [f for f in sc.functions if f.target is None and f.calls][:8]
    if hot:
        typer.echo("most-called functions it can't search yet:")
        for f in hot:
            typer.echo(f"  {f.calls:>12,}  {f.name:<32} {f.skipped}")


@app.command()
def scan(project_dir: Path = typer.Option(Path("."), "--project", help="directory with oqed.toml")):
    """Find every function in a project oqed can search, ranked by calls in the workload. Free."""
    from . import project

    _print_scan(project.scan(project.load(project_dir)))


def _target(fid: str):
    """A benchmark, or a project target from the last `oqed scan` here."""
    local = Path(".oqed/targets") / fid
    return load_target(local if local.is_dir() else fid)


@app.command()
def optimize(
    targets: list[str] = typer.Argument(None, help="benchmark names (default: this project)"),
    from_run: str = typer.Option(None, help="take winners from this run (id or path) instead of searching"),
    lock_path: Path = typer.Option(Path("oqed.lock"), "--lock", help="lock file to write or update"),
    max_usd: float = typer.Option(1.0, help="spending cap when searching"),
    unsafe: bool = typer.Option(
        False, help="also lock winners that passed testing but could not be proven (status `tested`)"
    ),
    top: int = typer.Option(None, help="project mode: search only the N most-called functions"),
):
    """Write winners to oqed.lock for oqed-cc. With no names and no --from-run,
    optimizes the whole project in this directory (see `oqed scan`)."""
    import asyncio
    import json

    from . import lock
    from .events import RUNS_DIR

    if from_run is None:
        from .providers import get_provider
        from .search import SearchConfig, run_search

        if targets:
            chosen = [_target(n) for n in targets]
        else:
            from . import project

            sc = project.scan(project.load(Path(".")))
            _print_scan(sc)
            locked = {e.function for e in lock.load(lock_path)} if lock_path.exists() else set()
            chosen = [load_target(f.target) for f in sc.targets[:top] if f.function not in locked]
            if skipped := [f.name for f in sc.targets[:top] if f.function in locked]:
                typer.echo(f"already in {lock_path}, not searched again: {', '.join(skipped)}")
            if not chosen:
                raise typer.Exit("nothing to search")
        from_run = asyncio.run(
            run_search(
                chosen,
                get_provider("spark-contributor"),
                get_provider("wonder"),
                SearchConfig(max_usd=max_usd),
                on_event=_print_event,
            )
        )
    path = Path(from_run) if from_run.endswith(".jsonl") else RUNS_DIR / f"{from_run}.jsonl"
    if not path.exists():
        path = RUNS_DIR / "demo" / path.name
    events = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
    config = next(e["config"] for e in events if e["type"] == "run_started")
    if config.get("bench", "call") != "inline":
        typer.secho("note: this run measured through call/ret; speedups are not inlined numbers", fg="yellow")
    asm = {e["candidate_id"]: e["asm"] for e in events if e["type"] == "candidate_proposed"}
    status = {e["candidate_id"]: e["status"] for e in events if e["type"] == "candidate_verified"}
    bench = {e["candidate_id"]: e for e in events if e["type"] == "candidate_benchmarked"}
    best: dict[str, str] = {}
    for e in events:
        if e["type"] == "new_best":
            best[e["function_id"]] = e["candidate_id"]  # the last new best is the incumbent

    new = []
    for fid, cid in best.items():
        if targets and fid not in targets:
            continue
        st = status.get(cid)
        if st != "proven" and not (unsafe and st == "tested_only"):
            hint = " (--unsafe locks it)" if st == "tested_only" else ""
            typer.echo(f"{fid}: skipped, best candidate is {st or 'unverified'}{hint}")
            continue
        t, b = _target(fid), bench[cid]
        new.append(
            lock.LockEntry(
                function=t.function,
                args=t.args,
                ret=t.ret,
                march=config.get("march", DEFAULT_MARCH),
                asm=asm[cid],
                status="proven" if st == "proven" else "tested",
                speedup=round(min(b["speedup_vs_clang"], b["speedup_vs_gcc"]), 2),
                benchmark=fid,
                run=path.stem,
                candidate=cid,
                oracle=t.oracle,
                slice_sha=t.slice_sha if t.oracle == "project" else None,
            )
        )
        flag = "" if st == "proven" else ", UNSAFE: tested only"
        typer.echo(f"{fid}: {t.function} <- {cid} ({new[-1].speedup}x{flag})")
    old = lock.load(lock_path) if lock_path.exists() else []
    lock_path.write_text(lock.dumps(lock.merge(old, new)))
    typer.echo(f"wrote {len(new)} entries to {lock_path}")


def main() -> None:
    app()
