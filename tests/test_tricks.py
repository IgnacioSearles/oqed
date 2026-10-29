import asyncio
from dataclasses import dataclass

from oqed import tricks
from oqed.events import read_events
from oqed.providers import Completion
from oqed.search import SearchConfig, run_search
from oqed.targets import load_target
from test_search import GOOD

T = load_target("round_up_pow2_u32")
OTHER = load_target("ceil_log2_u32")


@dataclass
class Recorder:
    model: str = "mock"

    def __post_init__(self):
        self.prompts: list[str] = []

    async def complete(self, messages, effort=None, max_tokens=0):
        self.prompts.append("\n".join(m["content"] for m in messages))
        return Completion(text=GOOD, model="mock", model_ms=0.0)


def _library(tmp_path):
    """A run that wins round_up_pow2_u32, turned into a library."""
    cfg = SearchConfig(candidates=1, waves=False, concurrency=1, tricks=0)
    run_id = asyncio.run(run_search([T], Recorder(), Recorder(), cfg, runs_dir=tmp_path))
    lib = tricks.build([tmp_path / f"{run_id}.jsonl"])
    tricks.save(lib, tmp_path / "tricks.jsonl")
    return lib


def test_wins_become_tricks_and_never_their_own_example(tmp_path):
    (t,) = _library(tmp_path)
    assert (t.function, t.project, t.status) == ("round_up_pow2_u32", "benchmarks", "proven")
    assert t.explanation == "lzcnt + 64-bit shrx" and "lzcnt" in t.asm and "round_up_pow2_u32" in t.c
    assert tricks.similar(T.c_source, T.function, [t], 3) == []
    ((got, score),) = tricks.similar(OTHER.c_source, OTHER.function, [t], 3)
    assert got is t and score > 0


def test_the_prompt_shows_similar_wins_and_the_log_names_them(tmp_path):
    _library(tmp_path)
    rec = Recorder()
    cfg = SearchConfig(candidates=1, waves=False, concurrency=1, tricks_path=str(tmp_path / "tricks.jsonl"))
    run_id = asyncio.run(run_search([OTHER], rec, rec, cfg, runs_dir=tmp_path / "runs"))
    assert "Verified wins on other functions" in rec.prompts[0] and "lzcnt + 64-bit shrx" in rec.prompts[0]
    base = next(e for e in read_events(tmp_path / "runs" / f"{run_id}.jsonl") if e.type == "baseline_ready")
    assert [x["function"] for x in base.tricks] == ["round_up_pow2_u32"]

    rec = Recorder()
    cfg = SearchConfig(candidates=1, waves=False, concurrency=1, tricks=0)
    asyncio.run(run_search([OTHER], rec, rec, cfg, runs_dir=tmp_path / "runs"))
    assert "Verified wins" not in rec.prompts[0]


def test_definition_skips_the_slice_declarations():
    src = "typedef int T;\nint helper(int);\n\nstatic int\nf(int a)\n{\n\treturn helper(a);\n}\n"
    assert tricks.definition(src, "f") == "static int\nf(int a)\n{\n\treturn helper(a);\n}"
