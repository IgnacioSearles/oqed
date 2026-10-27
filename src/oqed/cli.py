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
            typer.secho(
                f"[{fn}] baseline latency: clang {e.clang.cycles.median:.2f}, "
                f"gcc {e.gcc.cycles.median:.2f} ticks/call",
                bold=True,
            )
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
                f"[{fn}]   {e.cycles.median:.2f} ± {e.cycles.spread:.2f} ticks/call "
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


def main() -> None:
    app()
