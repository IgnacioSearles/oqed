import pytest

from oqed import baseline
from oqed.isa import isa_violations
from oqed.targets import load_all, load_target
from oqed.toolchain import assemble, compile_to_asm, mca


@pytest.mark.parametrize("target", load_all(), ids=lambda t: t.name)
@pytest.mark.parametrize("compiler", ["clang", "gcc"])
def test_baselines_assemble_within_isa(target, compiler):
    asm = compile_to_asm(target, compiler)
    code = assemble(asm)
    assert code.endswith(b"\xc3")
    assert isa_violations(code, "x86-64-v3") == []


def test_isa_check_rejects_newer_extensions():
    assert isa_violations(assemble("\tvpopcntd zmm0, zmm1\n\tret\n"), "x86-64-v3")
    assert isa_violations(assemble("\tpclmulqdq xmm0, xmm1, 0\n\tret\n"), "x86-64-v3")
    assert isa_violations(assemble("\tlzcnt eax, edi\n\tret\n"), "x86-64-v2")
    assert not isa_violations(assemble("\tlzcnt eax, edi\n\tshrx rax, rcx, rax\n\tret\n"), "x86-64-v3")


def test_mca_latency_reflects_dependency_chain():
    t = load_target("round_up_pow2_u32")
    smeared = baseline.compute(t, "clang").mca
    lzcnt = mca(
        "\tlea eax, [rdi - 1]\n\tlzcnt eax, eax\n\tmovabs rcx, 0x100000000\n\tshrx rax, rcx, rax\n\tret\n", t
    )
    assert lzcnt.latency < smeared.latency / 2
