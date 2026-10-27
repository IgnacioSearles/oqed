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
    cfg = SearchConfig(candidates=20, concurrency=4, stall_after=3)
    run_id = asyncio.run(run_search([T], mock, mock, cfg, runs_dir=tmp_path))
    events = read_events(tmp_path / f"{run_id}.jsonl")
    kinds = Counter(e.type for e in events)
    stages = Counter(e.stage for e in events if e.type == "candidate_rejected")

    assert kinds["candidate_proposed"] == 20
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
