"""The zlib lock under both compilers: minigzip -9 compressing and
decompressing zlib's own sources, concatenated 32 times, built plain and
through oqed-cc with clang and with gcc. Each is `oqed report`: identical
output files required, 11 alternating runs. The search measured everything
with clang; under gcc oqed-cc re-checks each entry against gcc's build of the
function. Both entries are tested only (no proofs for code that touches
memory), so there is no proven-only row; `longest_match` alone is.

    git clone https://github.com/madler/zlib ~/oqed-sweep/zlib  # 71747d1
    uv run python sweep/zlib/compilers.py ~/oqed-sweep/zlib
    # writes sweep/zlib/compilers.json
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
    locks = {"all": entries, "longest_match": [e for e in entries if e.function == "longest_match"]}
    config = (HERE / "oqed.toml").read_text()
    results = []
    for cc in ["clang", "gcc"]:
        for name, es in locks.items():
            with tempfile.TemporaryDirectory(prefix="oqed-compilers-") as d:
                root = Path(d) / "zlib"
                skip = shutil.ignore_patterns(".oqed", ".git", "minigzip", "oqe_*")
                shutil.copytree(src, root, ignore=skip)
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
                f"{cc:5} {name:13} {row['plain']:7.2f} -> {row['oqed']:7.2f} {r['unit']}  "
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
