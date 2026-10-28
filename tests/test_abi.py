import pytest

from oqed import abi

# A loop calling `mix`, keeping its state in callee-saved registers; three
# pushes leave rsp 16-byte aligned at the call.
GOOD = """\tpush rbx
\tpush r12
\tpush r13
\tmov r12, rdi
\tmov r13d, esi
\txor ebx, ebx
\ttest r13d, r13d
\tjle 2f
1:
\tmov edi, dword ptr [r12]
\tcall mix
\tadd ebx, eax
\tadd r12, 4
\tdec r13d
\tjnz 1b
2:
\tmov eax, ebx
\tpop r13
\tpop r12
\tpop rbx
\tret
"""

CORRECT = {
    "loop with calls": GOOD,
    "leaf": "\tlea eax, [rdi+rsi]\n\tret\n",
    "tail call": "\tmov rdi, rsi\n\tjmp mix\n",
    "dummy push to align": "\tpush rbx\n\tcall mix\n\tpush rcx\n\tpop rcx\n\tpop rbx\n\tret\n",
    "register zeroed after a call": (
        "\tpush rbx\n\tcall mix\n\txor ecx, ecx\n\tadd eax, ecx\n\tpop rbx\n\tret\n"
    ),
    "sub rsp to align": "\tsub rsp, 8\n\tcall mix\n\tadd rsp, 8\n\tret\n",
    # rsp realigned with `and`: the stack can't be tracked, so nothing is claimed.
    "frame pointer": (
        "\tpush rbp\n\tmov rbp, rsp\n\tand rsp, -16\n\tcall mix\n\tmov rsp, rbp\n\tpop rbp\n\tret\n"
    ),
}

WRONG = {
    # The protobuf_c_message_pack bug: a loop counter in ecx across a call.
    "counter in ecx across a call": (
        GOOD.replace("mov r13d, esi", "mov ecx, esi").replace("dec r13d", "dec ecx"),
        "reads rcx after a call",
    ),
    "misaligned call": (
        GOOD.replace("\tpush r13\n", "").replace("\tpop r13\n", "").replace("r13", "r12"),
        "not 16-byte aligned",
    ),
    "rbx not restored": (GOOD.replace("\tpop rbx\n", "\tadd rsp, 8\n"), "without restoring rbx"),
    "rbx clobbered": ("\tmov rbx, rdi\n\tmov rax, rbx\n\tret\n", "without restoring rbx"),
    "stack not popped": ("\tpush rax\n\tret\n", "stack not restored"),
    "tail call with a frame": ("\tpush rbx\n\tjmp mix\n", "tail-calls with the stack not restored"),
    "garbage popped and used": (
        "\tpush rbx\n\tcall mix\n\tpush rcx\n\tpop rcx\n\tmov rax, rcx\n\tpop rbx\n\tret\n",
        "reads rcx after a call",
    ),
    "only one path wrong": (
        "\tpush rbx\n\ttest edi, edi\n\tjz 1f\n\tpop rbx\n\tret\n1:\n\tmov rbx, rsi\n\tadd rsp, 8\n\tret\n",
        "without restoring rbx",
    ),
}


@pytest.mark.parametrize("name", CORRECT)
def test_correct_functions_pass(name):
    assert abi.violations(CORRECT[name]) == []


@pytest.mark.parametrize("name", WRONG)
def test_calling_convention_violations_are_caught(name):
    body, want = WRONG[name]
    found = abi.violations(body)
    assert any(want in v for v in found), found
