"""The protobuf-c lock under both compilers: the showcase workload (bench.c's
size + pack, the one the search ran) built plain and through oqed-cc with
clang and with gcc, every entry and the proven entries only. Each is `oqed
report`: identical output required, 11 alternating runs. The search measured
everything with clang; under gcc oqed-cc re-checks each entry against gcc's
build of the function.

    uv run python sweep/protobuf-c/compilers.py ~/oqed-sweep/protobuf-c
    # writes sweep/protobuf-c/compilers.json
"""

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

from oqed import lock, project
from oqed.report import report

HERE = Path(__file__).resolve().parent


def main(src: Path) -> None:
    entries = lock.load(HERE / "oqed.lock")
    locks = {"all": entries, "proven": [e for e in entries if e.status == "proven"]}
    config = (HERE / "oqed.toml").read_text()
    results = []
    for cc in ["clang", "gcc"]:
        for name, es in locks.items():
            with tempfile.TemporaryDirectory(prefix="oqed-compilers-") as d:
                root = Path(d) / "protobuf-c"
                shutil.copytree(src, root, ignore=shutil.ignore_patterns(".oqed", ".git", "pbc_*"))
                (root / lock.LOCK_NAME).write_text(lock.dumps(es))
                (root / project.CONFIG).write_text(f'cc = "{cc}"\n' + config)
                r = report(project.load(root), log=lambda *_: None)
            kept = [n for n in r["notes"] if not re.search(r": (substituted|UNSAFE)", n)]
            row = {
                "compiler": cc,
                "lock": name,
                "entries": r["entries"],
                "applied": r["applied"],
                "kept_c": kept,
                "plain": r["plain"]["median"],
                "oqed": r["oqed"]["median"],
                "speedup": r["speedup"],
                "faster_runs": r["faster_runs"],
                "runs": len(r["plain"]["runs"]),
                "output_identical": r["valid"],
            }
            results.append(row)
            print(
                f"{cc:5} {name:6} {row['plain']:7.2f} -> {row['oqed']:7.2f} {r['unit']}  "
                f"{row['speedup']:.3f}x  faster in {row['faster_runs']}/{row['runs']}  "
                f"{row['applied']}/{row['entries']} applied  output "
                f"{'identical' if row['output_identical'] else 'DIFFERENT'}",
                flush=True,
            )
            for n in kept:
                print(f"    {n[:150]}", flush=True)
    (HERE / "compilers.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]).expanduser())
