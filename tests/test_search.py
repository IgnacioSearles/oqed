import asyncio
from collections import Counter

from oqed import prompt
from oqed.events import read_events
from oqed.providers import MockProvider
from oqed.search import SearchConfig, run_search
from oqed.targets import load_target
from oqed.toolchain import compile_to_asm

T = load_target("round_up_pow2_u32")

GOOD = """<explanation>lzcnt + 64-bit shrx</explanation>
<asm>
lea eax, [rdi - 1]
lzcnt eax, eax
movabs rcx, 0x100000000
shrx rax, rcx, rax
ret
</asm>"""
WRONG = """<explanation>32-bit shift</explanation>
<asm>
lea eax, [rdi - 1]
lzcnt eax, eax
mov ecx, 1
neg eax
shlx eax, ecx, eax
ret
</asm>"""
GARBAGE = "<explanation>?</explanation><asm>\nfrobnicate eax\nret\n</asm>"
AVX512 = "<explanation>avx512</explanation><asm>\nvpopcntd zmm0, zmm1\nret\n</asm>"


def test_parse_reply_variants():
    expl, asm = prompt.parse_reply(GOOD, T.function)
    assert expl == "lzcnt + 64-bit shrx"
    assert asm.splitlines()[0] == "\tlea eax, [rdi - 1]"
    fenced = "Here:\n```asm\nround_up_pow2_u32:\n  .p2align 4\n  mov eax, edi ; copy\n1:\n  ret\n```"
    assert prompt.parse_reply(fenced, T.function)[1] == "\tmov eax, edi\n1:\n\tret\n"


def test_search_loop_with_mock(tmp_path):
    clang = f"<explanation>baseline</explanation><asm>\n{compile_to_asm(T, 'clang')}</asm>"
    mock = MockProvider([GOOD, WRONG, GARBAGE, AVX512, clang], seed=1)
    cfg = SearchConfig(candidates=20, concurrency=4, stall_after=3, reasoning_attempts=100)
    run_id = asyncio.run(run_search([T], mock, mock, cfg, runs_dir=tmp_path))
    events = read_events(tmp_path / f"{run_id}.jsonl")
    kinds = Counter(e.type for e in events)
    stages = Counter(e.stage for e in events if e.type == "candidate_rejected")

    assert kinds["candidate_proposed"] == 20
    assert kinds["search_converged"] == 0
    # Every candidate that got a model reply has a timing record, not just the measured ones.
    assert kinds["timing_breakdown"] == 20
    assert stages["test"] >= 1 and stages["assemble"] >= 1 and stages["isa"] >= 1
    assert stages["duplicate"] >= 1
    assert kinds["new_best"] == 1
    summary = events[-1].summary[T.name]
    assert summary.status == "proven" and summary.speedup_vs_clang > 1.5
    # The wrong rewrite's counterexample is reported.
    assert any(e.counterexample == {"a": 0} for e in events if e.type == "candidate_rejected")


def test_faster_requires_margin_beyond_noise():
    from oqed.events import Cycles
    from oqed.search import _faster

    best = Cycles(median=5.7, spread=0.01)
    assert not _faster(Cycles(median=4.8, spread=2.0), best, 0.0)  # within noise
    assert _faster(Cycles(median=4.8, spread=0.1), best, 0.0)
    assert not _faster(Cycles(median=5.65, spread=0.0), best, 0.02)  # under the 2% bar


def test_search_converges_after_fruitless_reasoning(tmp_path):
    mock = MockProvider([GOOD, WRONG], seed=2)
    cfg = SearchConfig(candidates=200, concurrency=2, stall_after=3, reasoning_attempts=2)
    run_id = asyncio.run(run_search([T], mock, mock, cfg, runs_dir=tmp_path))
    events = read_events(tmp_path / f"{run_id}.jsonl")
    kinds = Counter(e.type for e in events)
    assert kinds["search_converged"] == 1
    assert kinds["search_stalled"] == 2
    assert kinds["candidate_proposed"] < 30
    converged = next(e for e in events if e.type == "search_converged")
    assert converged.candidates == kinds["candidate_proposed"]
    assert events[-1].summary[T.name].status == "proven"


def test_parents_are_population_members(tmp_path):
    mock = MockProvider([GOOD, WRONG, GARBAGE], seed=3)
    # Sequential, so each call sees the result of the one before.
    cfg = SearchConfig(candidates=12, concurrency=1, stall_after=100)
    run_id = asyncio.run(run_search([T], mock, mock, cfg, runs_dir=tmp_path))
    events = read_events(tmp_path / f"{run_id}.jsonl")
    proposed = [e for e in events if e.type == "candidate_proposed"]
    winners = {e.candidate_id for e in events if e.type == "candidate_verified"}
    assert proposed[0].parent_id is None
    assert all(p.parent_id is None or p.parent_id in winners for p in proposed)
    assert any(p.parent_id in winners for p in proposed)


def test_prompt_state_shows_parent_and_tried_ideas():
    best = [prompt.CandidateInfo("lzcnt eax, edi\nret\n", "lzcnt trick", 3.7, status="proven")]
    st = prompt.SearchState(best=best, near=[], failures=[], tried=["smear -> wrong for a=0"], parent=0)
    text = prompt.search_state(st, reasoning=False)
    assert "Parent: lzcnt trick (3.70 ticks/call, proven equivalent)" in text
    assert "- smear -> wrong for a=0" in text
    near = [prompt.CandidateInfo("mov eax, edi\nret\n", "branchless", 6.1)]
    st = prompt.SearchState(best=[], near=near, failures=[], tried=[], parent=0, best_report="R")
    text = prompt.search_state(st, reasoning=False)
    assert "Nothing has beaten -O3 yet" in text and "make it faster" in text
    assert "bottleneck" not in text
    st.best, st.best_report = best, "Cycles with backend pressure increase"
    text = prompt.search_state(st, reasoning=True)
    assert "Cycles with backend pressure increase" in text and "Parent:" not in text
