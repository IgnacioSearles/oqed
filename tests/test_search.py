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
    cfg = SearchConfig(
        waves=False, candidates=20, concurrency=4, stall_after=3, reasoning_attempts=100, repairs=0
    )
    run_id = asyncio.run(run_search([T], mock, mock, cfg, runs_dir=tmp_path))
    events = read_events(tmp_path / f"{run_id}.jsonl")
    kinds = Counter(e.type for e in events)
    stages = Counter(e.stage for e in events if e.type == "candidate_rejected")

    assert kinds["candidate_proposed"] == 20
    assert kinds["search_stopped"] == 0
    # Every candidate that got a model reply has a timing record, not just the measured ones.
    assert kinds["timing_breakdown"] == 20
    assert stages["test"] >= 1 and stages["assemble"] >= 1 and stages["isa"] >= 1
    assert stages["duplicate"] >= 1
    bests = [e.candidate_id for e in events if e.type == "new_best"]
    detail = [
        (
            e.type,
            getattr(e, "candidate_id", ""),
            getattr(e, "status", ""),
            getattr(e, "reason", ""),
            getattr(e, "cycles", ""),
        )
        for e in events
        if e.type in ("baseline_ready", "candidate_benchmarked", "candidate_verified", "new_best")
    ]
    assert len(bests) == 1, detail
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
    cfg = SearchConfig(waves=False, candidates=200, concurrency=2, stall_after=3, reasoning_attempts=2)
    run_id = asyncio.run(run_search([T], mock, mock, cfg, runs_dir=tmp_path))
    events = read_events(tmp_path / f"{run_id}.jsonl")
    kinds = Counter(e.type for e in events)
    assert kinds["search_stopped"] == 1
    assert kinds["search_stalled"] == 2
    assert kinds["candidate_proposed"] < 30
    converged = next(e for e in events if e.type == "search_stopped")
    assert converged.candidates == kinds["candidate_proposed"]
    assert events[-1].summary[T.name].status == "proven"


def test_parents_are_population_members(tmp_path):
    mock = MockProvider([GOOD, WRONG, GARBAGE], seed=3)
    # Sequential, so each call sees the result of the one before.
    cfg = SearchConfig(waves=False, candidates=12, concurrency=1, stall_after=100, repairs=0)
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
    assert "Parent: lzcnt trick (3.70 ticks, proven equivalent)" in text
    assert "- smear -> wrong for a=0" in text
    near = [prompt.CandidateInfo("mov eax, edi\nret\n", "branchless", 6.1)]
    st = prompt.SearchState(best=[], near=near, failures=[], tried=[], parent=0, best_report="R")
    text = prompt.search_state(st, reasoning=False)
    assert "Nothing has beaten -O3 yet" in text and "make it faster" in text
    assert "bottleneck" not in text
    st.best, st.best_report = best, "Cycles with backend pressure increase"
    text = prompt.search_state(st, reasoning=True)
    assert "Cycles with backend pressure increase" in text and "Parent:" not in text


def test_spending_cap_stops_the_run(tmp_path):
    mock = MockProvider([GOOD, WRONG, GARBAGE], seed=4, cost=0.1)
    cfg = SearchConfig(waves=False, candidates=50, concurrency=2, stall_after=100, max_usd=0.5)
    other = load_target("parity_u32")
    run_id = asyncio.run(run_search([T, other], mock, mock, cfg, runs_dir=tmp_path))
    events = read_events(tmp_path / f"{run_id}.jsonl")
    kinds = Counter(e.type for e in events)
    stopped = next(e for e in events if e.type == "search_stopped")
    assert "spending cap" in stopped.reason
    # Calls reserve their worst case before starting, so the cap is never exceeded.
    assert kinds["candidate_proposed"] == 5
    assert abs(events[-1].cost_usd - 0.5) < 1e-9
    assert set(events[-1].summary) == {T.name}  # the second function never started


def test_reasoning_tier_is_skipped_when_its_worst_case_does_not_fit(tmp_path, capsys):
    volume = MockProvider([WRONG], seed=0, cost=0.01)
    reasoning = MockProvider([GOOD], seed=0, model="mock-reasoning", cost=0.3, worst=0.7)
    cfg = SearchConfig(waves=False, candidates=20, concurrency=1, stall_after=3, max_usd=0.25)
    run_id = asyncio.run(run_search([T], volume, reasoning, cfg, runs_dir=tmp_path))
    events = read_events(tmp_path / f"{run_id}.jsonl")
    assert not any(e.type == "candidate_proposed" and e.model == "mock-reasoning" for e in events)
    assert events[-1].cost_usd <= 0.25
    assert "mock-reasoning not called" in capsys.readouterr().out


def test_candidates_not_faster_than_a_proven_best_skip_the_proof(tmp_path):
    # Correct and faster than -O3, but a few extra instructions slower than GOOD.
    pad = "mov edx, eax\nimul edx, edx\n" * 2
    slower = GOOD.replace("ret\n</asm>", pad + "ret\n</asm>")
    slower = slower.replace("lzcnt + 64-bit shrx", "padded")
    mock = MockProvider([GOOD, slower], seed=1)
    cfg = SearchConfig(waves=False, candidates=8, concurrency=1, stall_after=100)
    run_id = asyncio.run(run_search([T], mock, mock, cfg, runs_dir=tmp_path))
    events = read_events(tmp_path / f"{run_id}.jsonl")
    proposed = {e.candidate_id: e.explanation for e in events if e.type == "candidate_proposed"}
    verified = [e for e in events if e.type == "candidate_verified"]
    best = [e.candidate_id for e in events if e.type == "new_best"]
    assert [proposed[c] for c in best] == ["lzcnt + 64-bit shrx"]
    after = [e for e in verified if proposed[e.candidate_id] == "padded" and e.candidate_id > best[0]]
    assert after, "seed should propose the padded candidate after the best"
    assert all(e.status == "tested_only" and "proof skipped" in e.reason for e in after)


def test_stack_using_candidate_is_rejected_as_not_inlinable(tmp_path):
    # Correct (passes testing and could be proven) but pushes to the stack.
    stack = GOOD.replace("<asm>\n", "<asm>\npush rbx\npop rbx\n")
    mock = MockProvider([stack], seed=0)
    cfg = SearchConfig(waves=False, candidates=1, concurrency=1, stall_after=100)
    run_id = asyncio.run(run_search([T], mock, mock, cfg, runs_dir=tmp_path))
    events = read_events(tmp_path / f"{run_id}.jsonl")
    rej = [e for e in events if e.type == "candidate_rejected"]
    assert [e.stage for e in rej] == ["inline"]
    assert rej[0].reason.startswith("not inlinable: uses the stack pointer")


def test_inlined_speedups_compare_against_c_in_the_same_loop(tmp_path):
    mock = MockProvider([GOOD], seed=0)
    cfg = SearchConfig(waves=False, candidates=1, concurrency=1, stall_after=100)
    run_id = asyncio.run(run_search([T], mock, mock, cfg, runs_dir=tmp_path))
    events = read_events(tmp_path / f"{run_id}.jsonl")
    run = next(e for e in events if e.type == "run_started")
    assert run.config["bench"] == "inline"
    b = next(e for e in events if e.type == "candidate_benchmarked")
    # The lzcnt rewrite is ~3x faster than the smearing C in the dependent loop.
    assert min(b.speedup_vs_clang, b.speedup_vs_gcc) > 1.5


def test_waves_repair_then_stop_when_nothing_is_correct(tmp_path):
    volume = MockProvider([WRONG, GARBAGE], seed=0)
    reasoning = MockProvider([WRONG], seed=0, model="mock-reasoning")
    cfg = SearchConfig(candidates=40, repairs=0)
    run_id = asyncio.run(run_search([T], volume, reasoning, cfg, runs_dir=tmp_path))
    events = read_events(tmp_path / f"{run_id}.jsonl")
    proposed = [e for e in events if e.type == "candidate_proposed"]
    # Nothing correct: the first wave, then repair waves with the errors shown,
    # the last one led by the reasoning tier.
    assert len(proposed) == cfg.first_wave + cfg.repair_waves * cfg.wave
    assert sum(e.model == "mock-reasoning" for e in proposed) == 1
    stopped = next(e for e in events if e.type == "search_stopped")
    assert "nothing beat the C" in stopped.reason


def test_waves_refine_then_escalate_once_then_stop(tmp_path):
    volume = MockProvider([GOOD], seed=0)
    reasoning = MockProvider([GOOD], seed=0, model="mock-reasoning")
    cfg = SearchConfig(candidates=40)
    run_id = asyncio.run(run_search([T], volume, reasoning, cfg, runs_dir=tmp_path))
    events = read_events(tmp_path / f"{run_id}.jsonl")
    proposed = [e for e in events if e.type == "candidate_proposed"]
    # First wave finds the best; a refinement wave doesn't improve; one wave led
    # by the reasoning tier doesn't either; stop.
    assert len(proposed) == cfg.first_wave + 2 * cfg.wave
    assert sum(e.model == "mock-reasoning" for e in proposed) == 1
    assert "no improvement" in next(e for e in events if e.type == "search_stopped").reason
    assert events[-1].summary[T.name].status == "proven"


def test_waves_search_several_functions_at_once(tmp_path):
    mock = MockProvider([WRONG], seed=0)
    other = load_target("parity_u32")
    cfg = SearchConfig(candidates=40)
    run_id = asyncio.run(run_search([T, other], mock, mock, cfg, runs_dir=tmp_path))
    events = read_events(tmp_path / f"{run_id}.jsonl")
    assert list(events[-1].summary) == [T.name, other.name]
    assert sum(e.type == "candidate_proposed" for e in events) == 2 * (
        cfg.first_wave + cfg.repair_waves * cfg.wave
    )


def test_candidates_that_do_not_assemble_go_back_with_their_errors(tmp_path):
    from dataclasses import dataclass

    from oqed.providers import Completion

    bad = GOOD.replace("<asm>\n", "<asm>\nadd r10, edx\n")
    asked: list[str] = []

    @dataclass
    class Fixer:
        model: str = "mock"

        async def complete(self, messages, effort=None, max_tokens=0):
            last = messages[-1]["content"]
            asked.append(last)
            return Completion(text=GOOD if "doesn't assemble" in last else bad, model="mock", model_ms=0.0)

    cfg = SearchConfig(candidates=1, waves=False, concurrency=1)
    run_id = asyncio.run(run_search([T], Fixer(), Fixer(), cfg, runs_dir=tmp_path))
    events = read_events(tmp_path / f"{run_id}.jsonl")
    rejected = [e for e in events if e.type == "candidate_rejected"]
    assert rejected[0].stage == "assemble" and "line 1 `add r10, edx`" in rejected[0].reason
    assert "line 1 `add r10, edx`" in asked[1] and "same width" in asked[1]
    proposed = [e for e in events if e.type == "candidate_proposed"]
    assert proposed[1].candidate_id == proposed[0].candidate_id + "r"
    assert proposed[1].parent_id == proposed[0].candidate_id
    assert events[-1].summary[T.name].best_candidate_id == proposed[1].candidate_id
