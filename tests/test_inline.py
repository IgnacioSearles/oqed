import pytest

from oqed import inline
from oqed.targets import load_target
from oqed.toolchain import ToolError, assemble, compile_to_asm

HIGH_BIT = "\tlzcnt eax, edi\n\tsbb ecx, ecx\n\txor eax, 31\n\tor eax, ecx\n\tret\n"
# clamp_u8 with a branch and an early return.
CLAMP_BRANCHY = (
    "\txor eax, eax\n\ttest edi, edi\n\tjs .Ldone\n\tmov eax, 255\n\tcmp edi, eax\n"
    "\tjg .Ldone\n\tmov eax, edi\n\tret\n.Ldone:\n\tret\n"
)


def test_written_registers_include_implicit_operands():
    assert inline.written_registers(assemble("\tmov eax, edi\n\tmul esi\n\tret\n")) == {"rax", "rdx"}
    assert inline.written_registers(assemble("\tvmovd xmm3, edi\n\tvmovd eax, xmm3\n\tret\n")) == {
        "rax",
        "zmm3",
    }


@pytest.mark.parametrize(
    "body", ["\tmov eax, [rdi]\n\tret\n", "\tpush rbx\n\tmov eax, edi\n\tpop rbx\n\tret\n"]
)
def test_memory_and_stack_are_not_inlinable(body):
    with pytest.raises(inline.NotInlinable):
        inline.written_registers(assemble(body))


def test_template_makes_labels_unique_and_rets_jump_to_the_end():
    tpl = inline._template(CLAMP_BRANCHY)
    assert "js .Ldone_%=" in tpl and ".Ldone_%=:" in tpl
    assert "jmp .Loqed_end_%=" in tpl and tpl.endswith(".Loqed_end_%=:\\n")
    assert "ret" not in tpl


def test_inline_function_binds_arguments_and_clobbers():
    src = inline.inline_function(HIGH_BIT, load_target("stbi_high_bit"), "cand")
    assert '"=a"(__oqed_r)' in src and '"D"(__oqed_v0)' in src
    assert '"rcx", "cc"' in src


def test_written_registers_become_operands_or_clobbers():
    # mulhi takes (a, b) in rdi/rsi: rdi is written (in-out), rdx is a scratch clobber.
    body = "\tmov eax, edi\n\tmul esi\n\tmov edi, edx\n\tmov eax, edi\n\tret\n"
    src = inline.inline_function(body, load_target("mulhi_u32_portable"), "cand")
    assert '"+D"(__oqed_v0)' in src and '"S"(__oqed_v1)' in src
    assert '"rdx", "cc"' in src


def test_measure_and_reverify_a_branchy_candidate():
    t = load_target("clamp_u8")
    r = inline.measure(CLAMP_BRANCHY, t, "clang", "x86-64-v3")
    assert all(getattr(r, v).median > 0.1 for v in inline.VARIANTS)
    assert inline.reverify(CLAMP_BRANCHY, t, "gcc", "x86-64-v3").status == "equivalent"


def test_compiler_output_inlines_as_itself():
    t = load_target("abs32")
    body = compile_to_asm(t, "gcc")
    r = inline.measure(body, t, "gcc", "x86-64-v3")
    assert 0.8 < r.dep_ref.median / r.dep_cand.median < 1.25


def test_wrong_candidate_fails_the_in_program_check():
    with pytest.raises(ToolError, match="disagrees"):
        inline.measure("\tmov eax, edi\n\tret\n", load_target("abs32"), "clang", "x86-64-v3")


def test_numeric_local_labels_become_named():
    # `1f` would be read as a binary number by clang's Intel-syntax parser.
    body = "\ttest edi, -2\n\tjz 1f\n\tpopcnt eax, edi\n\tand eax, 1\n\tret\n1:\n\tmov eax, edi\n\tret\n"
    tpl = inline._template(body)
    assert "jz oqed_n0_%=" in tpl and "and eax, 1\\n" in tpl and "\toqed_n0_%=:\\n" in tpl
    r = inline.measure(body, load_target("parity_u32"), "clang", "x86-64-v3")
    assert r.dep_cand.median > 0
