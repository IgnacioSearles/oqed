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


def main() -> None:
    app()
