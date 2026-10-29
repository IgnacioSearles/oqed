"""Transfer check: the protobuf-c lock (found on bench.c's size + pack workload)
on workloads the search never ran (projects/protobuf_c/transfer.c), with every
entry and with the proven entries only. Each is `oqed report`: plain build vs
oqed-cc build, identical output required, 11 alternating runs.

    uv run python sweep/protobuf-c/transfer.py   # writes sweep/protobuf-c/transfer.json
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from oqed import lock, project
from oqed.report import report

HERE = Path(__file__).resolve().parent
TESTBED = HERE.parents[1] / "projects" / "protobuf_c"
WORKLOADS = ["mixed", "small", "large", "cover", "unpack"]
SOURCES = "transfer.c protobuf-c.c telemetry.pb-c.c cover.pb-c.c"


def main() -> None:
    entries = lock.load(HERE / "oqed.lock")
    locks = {"all": entries, "proven": [e for e in entries if e.status == "proven"]}
    files = subprocess.run(
        ["git", "ls-files", "."], cwd=TESTBED, capture_output=True, text=True, check=True
    ).stdout.split()
    results = []
    for w in WORKLOADS:
        for name, es in locks.items():
            with tempfile.TemporaryDirectory(prefix="oqed-transfer-") as d:
                root = Path(d) / "protobuf-c"
                root.mkdir()
                for f in {*files, "transfer.c"}:
                    shutil.copy(TESTBED / f, root / f)
                (root / lock.LOCK_NAME).write_text(lock.dumps(es))
                (root / project.CONFIG).write_text(
                    f'build = "$CC -O3 -march=x86-64-v3 -o pbc_transfer {SOURCES}"\n'
                    'run = "./pbc_transfer --check"\n'
                    'test = "./pbc_transfer --check"\n'
                    f'bench = "./pbc_transfer {w}"\n'
                    'bench_unit = "ns/message"\n'
                )
                r = report(project.load(root), log=lambda *_: None)
            row = {
                "workload": w,
                "lock": name,
                "entries": r["entries"],
                "applied": r["applied"],
                "plain": r["plain"]["median"],
                "oqed": r["oqed"]["median"],
                "speedup": r["speedup"],
                "faster_runs": r["faster_runs"],
                "runs": len(r["plain"]["runs"]),
                "output_identical": r["valid"],
            }
            results.append(row)
            print(
                f"{w:7} {name:6} {row['plain']:10.2f} -> {row['oqed']:10.2f} ns/message  "
                f"{row['speedup']:.3f}x  faster in {row['faster_runs']}/{row['runs']}  "
                f"{row['applied']}/{row['entries']} applied  output "
                f"{'identical' if row['output_identical'] else 'DIFFERENT'}",
                flush=True,
            )
    (HERE / "transfer.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
