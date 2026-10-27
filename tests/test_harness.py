import pytest

from oqed import harness
from oqed.isa import forbidden_instructions
from oqed.targets import load_all, load_target
from oqed.toolchain import assemble, compile_to_asm

ROUND_UP = load_target("round_up_pow2_u32")


def code(*insns: str) -> bytes:
    return assemble("".join(f"\t{i}\n" for i in insns))


@pytest.fixture(scope="module")
def round_up_ref() -> bytes:
    return assemble(compile_to_asm(ROUND_UP, "clang"))


@pytest.mark.parametrize("target", load_all(), ids=lambda t: t.name)
def test_clang_and_gcc_agree(target):
    """Self-check: two independent compilations of the same C must agree."""
    clang = assemble(compile_to_asm(target, "clang"))
    gcc = assemble(compile_to_asm(target, "gcc"))
    res = harness.difftest(target, clang, gcc)
    assert res.passed, res


def test_correct_rewrite_passes(round_up_ref):
    cand = code(
        "lea eax, [rdi - 1]", "lzcnt eax, eax", "movabs rcx, 0x100000000", "shrx rax, rcx, rax", "ret"
    )
    assert harness.difftest(ROUND_UP, round_up_ref, cand).passed


def test_wrong_shift_width_is_caught(round_up_ref):
    # 32-bit shift counts wrap mod 32, so x=0 gives 1 instead of 0.
    cand = code("lea eax, [rdi - 1]", "lzcnt eax, eax", "mov ecx, 1", "neg eax", "shlx eax, ecx, eax", "ret")
    res = harness.difftest(ROUND_UP, round_up_ref, cand)
    assert not res.passed
    assert res.counterexample.args == {"a": 0}
    assert (res.counterexample.expected, res.counterexample.got) == (0, 1)


def test_reading_undefined_upper_bits_is_caught(round_up_ref):
    cand = code(
        "lea rax, [rdi - 1]",
        "lzcnt rax, rax",
        "mov ecx, 1",
        "neg eax",
        "add eax, 64",
        "shlx rax, rcx, rax",
        "ret",
    )
    res = harness.difftest(ROUND_UP, round_up_ref, cand)
    assert not res.passed
    assert res.counterexample.needs_upper_bits


def test_narrow_return_compared_at_width():
    t = load_target("clamp_u8")
    ref = assemble(compile_to_asm(t, "clang"))
    # Same low 8 bits, junk above: valid for a uint8_t return.
    cand = code(
        "mov eax, edi",
        "sar eax, 31",
        "not eax",
        "test edi, 0xffffff00",
        "cmove eax, edi",
        "or eax, 0x1200",
        "ret",
    )
    assert harness.difftest(t, ref, cand).passed


def test_clobbered_callee_saved_register_is_caught(round_up_ref):
    cand = code(
        "mov rbx, rdi",
        "lea eax, [rbx - 1]",
        "lzcnt eax, eax",
        "movabs rcx, 0x100000000",
        "shrx rax, rcx, rax",
        "ret",
    )
    res = harness.difftest(ROUND_UP, round_up_ref, cand)
    assert not res.passed
    assert "rbx" in res.reason


def test_crash_and_timeout_are_rejections(round_up_ref):
    crash = harness.difftest(ROUND_UP, round_up_ref, code("ud2"))
    assert not crash.passed and "SIGILL" in crash.reason
    hang = harness.difftest(ROUND_UP, round_up_ref, code("1:", "jmp 1b"))
    assert not hang.passed and hang.reason == "timed out"


def test_forbidden_instructions():
    assert forbidden_instructions(code("syscall", "ret")) == ["syscall"]
    assert forbidden_instructions(code("rdtsc", "ret")) == ["rdtsc"]
    assert forbidden_instructions(code("mov eax, edi", "ret")) == []


def test_edge_values_respect_type():
    assert harness.edge_values("u8") == sorted(set(harness.edge_values("u8")))
    assert all(0 <= v <= 255 for v in harness.edge_values("u8"))
    assert min(harness.edge_values("i32")) == -(2**31)
    assert max(harness.edge_values("u64")) == 2**64 - 1


def test_measure_reports_spread(round_up_ref):
    c = harness.measure(round_up_ref, ROUND_UP, reps=21, iters=500)
    assert c.median > 0 and c.spread >= 0
