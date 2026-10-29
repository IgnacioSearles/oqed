"""The trick library: verified wins, shown to the model as examples.

Every function a run won (the last `new_best`, proven or tested) becomes a
trick: the C it replaced, the asm, the model's one-line explanation, its
status and speedup, and where it was found. `oqed tricks` collects them from
the run logs into `tricks.jsonl`; the search puts the few most similar ones
into each function's prompt (`SearchConfig.tricks`). No weights change: the
models learn from them in context.

Similarity is lexical: TF-IDF over the tokens of the C function (identifiers,
numbers, operators), cosine. A function never sees its own trick, so a
library built from earlier runs can be measured on functions it doesn't hold.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from .events import RUNS_DIR
from .targets import BENCHMARKS_DIR

LIBRARY = RUNS_DIR.parent / "tricks.jsonl"
MAX_C_LINES = 40
MAX_ASM_LINES = 60
_TOKEN = re.compile(r"[A-Za-z_]\w*|0x[0-9a-fA-F]+|\d+|<<|>>|[-+*/%&|^~!<>=?]")


@dataclass
class Trick:
    id: str  # run:candidate
    function: str
    project: str  # "benchmarks" for the benchmark set
    c: str  # the function's own definition
    asm: str
    explanation: str
    status: str  # proven / tested
    speedup: float | None  # what the search measured for it
    model: str

    def tokens(self) -> Counter:
        return Counter(_TOKEN.findall(self.c))


def definition(source: str, name: str) -> str:
    """The function's own definition in a slice (which puts the declarations
    it uses first), from its first line to the end."""
    lines = source.strip().splitlines()
    pat = re.compile(rf"\b{re.escape(name)}\s*\(")
    for i in range(len(lines) - 1, -1, -1):
        ln = lines[i]
        if not pat.search(ln) or ln[:1].isspace() or ln.rstrip().endswith(";"):
            continue
        start = i
        while start > 0 and lines[start - 1].strip() and not re.search(r"[;{}]\s*$|^#", lines[start - 1]):
            start -= 1
        return "\n".join(lines[start:])
    return source.strip()


def from_run(path: Path) -> list[Trick]:
    events = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
    if not events or events[0].get("config", {}).get("fake"):
        return []
    project = next((e["project"] for e in events if e["type"] == "project_scanned"), None)
    source = {e["function_id"]: e["source"] for e in events if e["type"] == "baseline_ready"}
    proposed = {e["candidate_id"]: e for e in events if e["type"] == "candidate_proposed"}
    status = {e["candidate_id"]: e["status"] for e in events if e["type"] == "candidate_verified"}
    bench = {e["candidate_id"]: e for e in events if e["type"] == "candidate_benchmarked"}
    best: dict[str, str] = {}
    for e in events:
        if e["type"] == "new_best":
            best[e["function_id"]] = e["candidate_id"]
    out = []
    for fid, cid in best.items():
        p, b = proposed.get(cid), bench.get(cid)
        if p is None or fid not in source:
            continue
        name = fid.split(".")[0]
        # Project runs from before the scan was logged don't name their project.
        where = project or ("benchmarks" if (BENCHMARKS_DIR / fid).is_dir() else "a C project")
        expl = re.sub(r"\s*\[oqed fixed:.*\]$", "", p["explanation"]).strip()
        out.append(
            Trick(
                id=f"{path.stem}:{cid}",
                function=name,
                project=where,
                c=definition(source[fid], name),
                asm=p["asm"].strip(),
                explanation=expl,
                status="proven" if status.get(cid) == "proven" else "tested",
                speedup=round(min(b["speedup_vs_clang"], b["speedup_vs_gcc"]), 3) if b else None,
                model=p["model"],
            )
        )
    return out


def build(paths: list[Path]) -> list[Trick]:
    """Tricks from run logs; the latest win per function."""
    by_fn: dict[str, Trick] = {}
    for path in sorted(paths, key=lambda p: p.stem):
        for t in from_run(path):
            by_fn[t.function] = t
    return sorted(by_fn.values(), key=lambda t: (t.project, t.function))


def save(tricks: list[Trick], path: Path = LIBRARY) -> None:
    path.write_text("".join(json.dumps(asdict(t)) + "\n" for t in tricks))


def load(path: Path = LIBRARY) -> list[Trick]:
    if not path.exists():
        return []
    return [Trick(**json.loads(ln)) for ln in path.read_text().splitlines() if ln.strip()]


def similar(c_source: str, function: str, library: list[Trick], k: int) -> list[tuple[Trick, float]]:
    """The k tricks most like this function, never its own."""
    pool = [t for t in library if t.function != function]
    if not pool or k <= 0:
        return []
    docs = [t.tokens() for t in pool]
    df = Counter(tok for d in docs for tok in d)
    idf = {tok: math.log((1 + len(docs)) / (1 + n)) + 1 for tok, n in df.items()}

    def vec(c: Counter) -> dict[str, float]:
        v = {tok: (1 + math.log(n)) * idf.get(tok, math.log(1 + len(docs)) + 1) for tok, n in c.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {tok: x / norm for tok, x in v.items()}

    q = vec(Counter(_TOKEN.findall(definition(c_source, function))))
    scored = []
    for t, d in zip(pool, docs, strict=True):
        v = vec(d)
        scored.append((t, sum(x * v.get(tok, 0.0) for tok, x in q.items())))
    scored.sort(key=lambda s: -s[1])
    return scored[:k]


def _clip(text: str, n: int, what: str) -> str:
    lines = text.strip().splitlines()
    return "\n".join(lines[:n] + ([f"... ({len(lines) - n} more lines of {what})"] if len(lines) > n else []))


def prompt_section(chosen: list[tuple[Trick, float]]) -> str:
    """Verified wins on other functions, for the stable part of the prompt."""
    if not chosen:
        return ""
    parts = [
        "Verified wins on other functions (from oqed's trick library). They show tricks that worked "
        "on similar code; use an idea if it fits this function, never copy code that doesn't apply."
    ]
    for t, _ in chosen:
        sp = f"{t.speedup:.2f}x faster, " if t.speedup else ""
        parts.append(
            f"`{t.function}` ({t.project}; {sp}{'proven equivalent' if t.status == 'proven' else 'tested'}): "
            f"{t.explanation}\n```c\n{_clip(t.c, MAX_C_LINES, 'C')}\n```\n```asm\n"
            f"{_clip(t.asm, MAX_ASM_LINES, 'asm')}\n```"
        )
    return "\n\n".join(parts)
