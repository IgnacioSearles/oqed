"""The verifier must never call a wrong rewrite equivalent.

Each wrong rewrite below is a realistic mistake. Most also fail differential
testing; `test_single_input_bug_needs_the_proof` is one that testing misses.
"""

import logging

import pytest

from oqed import harness
from oqed.targets import load_all, load_target
from oqed.toolchain import assemble, compile_to_asm
from oqed.verify import check_sync

logging.getLogger("angr").setLevel(logging.ERROR)


def code(*insns: str) -> bytes:
    return assemble("".join(f"\t{i}\n" if not i.endswith(":") else f"{i}\n" for i in insns))


def clang(name: str) -> bytes:
    return assemble(compile_to_asm(load_target(name), "clang"))


ROUND_UP_GOOD = (
    "lea eax, [rdi - 1]",
    "lzcnt eax, eax",
    "movabs rcx, 0x100000000",
    "shrx rax, rcx, rax",
    "ret",
)

WRONG = {
    "shift_count_wraps": (
        "round_up_pow2_u32",
        ("lea eax, [rdi - 1]", "lzcnt eax, eax", "mov ecx, 1", "neg eax", "shlx eax, ecx, eax", "ret"),
    ),
    "reads_undefined_upper_bits": (
        "round_up_pow2_u32",
        (
            "lea rax, [rdi - 1]",
            "lzcnt rax, rax",
            "mov ecx, 1",
            "neg eax",
            "add eax, 64",
            "shlx rax, rcx, rax",
            "ret",
        ),
    ),
    "off_by_one_missing_decrement": (
        "round_up_pow2_u32",
        ("mov eax, edi", "lzcnt eax, eax", "movabs rcx, 0x100000000", "shrx rax, rcx, rax", "ret"),
    ),
    "wrong_flag_setge_for_setg": (
        "sign32",
        ("xor eax, eax", "test edi, edi", "setge al", "shr edi, 31", "sub eax, edi", "ret"),
    ),
    "wrong_sign_extension": (
        "avg_floor_u32",
        ("movsxd rax, edi", "movsxd rcx, esi", "add rax, rcx", "shr rax, 1", "ret"),
    ),
    "overflow_in_32_bits": ("avg_floor_u32", ("lea eax, [rdi + rsi]", "shr eax, 1", "ret")),
    "clobbers_callee_saved_rbx": (
        "round_up_pow2_u32",
        ("lea ebx, [rdi - 1]", "lzcnt eax, ebx", "movabs rcx, 0x100000000", "shrx rax, rcx, rax", "ret"),
    ),
    "unbalanced_stack": (
        "isolate_lowest_u32",
        ("push rbx", "mov eax, edi", "neg eax", "and eax, edi", "ret"),
    ),
    "reads_garbage_register": (
        "isolate_lowest_u32",
        ("mov eax, edi", "neg eax", "and eax, edi", "xor eax, r8d", "ret"),
    ),
    "single_input_bug": (
        "abs32",
        (
            "mov eax, edi",
            "neg eax",
            "cmovs eax, edi",
            "cmp edi, 0x2a2a2a2a",
            "jne 1f",
            "inc eax",
            "1:",
            "ret",
        ),
    ),
    "bsr_undefined_on_zero": ("floor_log2_smear_u32", ("bsr eax, edi", "ret")),
    "absolute_memory_access": ("isolate_lowest_u32", ("mov eax, dword ptr [0x1000]", "ret")),
    "correct_but_loops": (
        "popcount_swar_u32",
        ("xor eax, eax", "1:", "test edi, edi", "jz 2f", "blsr edi, edi", "inc eax", "jmp 1b", "2:", "ret"),
    ),
}


@pytest.mark.parametrize("case", WRONG, ids=str)
def test_wrong_rewrite_is_never_proven(case):
    name, insns = WRONG[case]
    r = check_sync(load_target(name), clang(name), code(*insns))
    assert r.status != "equivalent", r
    if r.status == "not_equivalent":
        assert r.counterexample is not None


def test_not_equivalent_counterexamples_are_concrete():
    t = load_target("round_up_pow2_u32")
    r = check_sync(t, clang(t.name), code(*WRONG["reads_undefined_upper_bits"][1]))
    assert r.status == "not_equivalent"
    assert "upper bits" in r.reason


def test_single_input_bug_needs_the_proof():
    t = load_target("abs32")
    cand = code(*WRONG["single_input_bug"][1])
    assert harness.difftest(t, clang(t.name), cand).passed  # testing misses it
    r = check_sync(t, clang(t.name), cand)
    assert r.status == "not_equivalent"
    assert r.counterexample == {"a": 0x2A2A2A2A}


def test_unsupported_cases_are_unknown_not_rejected_as_wrong():
    for case in ("bsr_undefined_on_zero", "absolute_memory_access", "correct_but_loops"):
        name, insns = WRONG[case]
        assert check_sync(load_target(name), clang(name), code(*insns)).status == "unknown", case


GOOD = {
    "lzcnt_round_up": ("round_up_pow2_u32", ROUND_UP_GOOD),
    "garbage_read_cancels_out": (
        "isolate_lowest_u32",
        ("mov eax, edi", "neg eax", "and eax, edi", "add eax, r8d", "sub eax, r8d", "ret"),
    ),
    "junk_above_narrow_return": (
        "clamp_u8",
        (
            "mov eax, edi",
            "sar eax, 31",
            "not eax",
            "test edi, 0xffffff00",
            "cmove eax, edi",
            "or eax, 0x1200",
            "ret",
        ),
    ),
    "saves_and_restores_rbx": (
        "isolate_lowest_u32",
        ("push rbx", "mov ebx, edi", "neg ebx", "and ebx, edi", "mov eax, ebx", "pop rbx", "ret"),
    ),
    "parity_popcnt": ("parity_u32", ("popcnt eax, edi", "and eax, 1", "ret")),
}


@pytest.mark.parametrize("case", GOOD, ids=str)
def test_correct_rewrite_is_proven(case):
    name, insns = GOOD[case]
    r = check_sync(load_target(name), clang(name), code(*insns))
    assert r.status == "equivalent", r


@pytest.mark.parametrize("target", load_all(), ids=lambda t: t.name)
def test_clang_and_gcc_are_proven_equivalent(target):
    gcc = assemble(compile_to_asm(target, "gcc"))
    r = check_sync(target, clang(target.name), gcc)
    assert r.status == "equivalent", r


def test_restart_does_not_break_other_checks():
    """A timeout elsewhere kills the whole pool; checks running in it retry."""
    import threading
    import time

    from oqed.verify import Verifier

    t = load_target("mulhi_u32_portable")
    slow = code("mov eax, edi", "mul esi", "mov eax, edx", "ret")  # nonlinear: runs to the solver limit
    v = Verifier(workers=2, timeout_s=4)
    try:
        out = {}
        th = threading.Thread(target=lambda: out.setdefault("r", v.check(t, clang(t.name), slow)))
        th.start()
        time.sleep(1.5)
        v._restart(v._pool)  # what a timed-out check does
        th.join()
        assert out["r"].status in ("unknown", "equivalent")
        rt = load_target("round_up_pow2_u32")
        assert v.check(rt, clang(rt.name), code(*ROUND_UP_GOOD)).status == "equivalent"
    finally:
        v.close()
