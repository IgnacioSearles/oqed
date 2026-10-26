"""oqed command-line interface."""

from __future__ import annotations

import typer

from . import baseline, fake
from .targets import load_all, load_target
from .toolchain import DEFAULT_MARCH, DEFAULT_MCPU

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


def main() -> None:
    app()
