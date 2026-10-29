import pytest

from oqed import baseline
from oqed.isa import isa_violations
from oqed.targets import Target, load_all, load_target
from oqed.toolchain import assemble, compile_to_asm, mca, record_layouts


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


def test_record_layouts_lists_the_slices_own_fields(tmp_path):
    src = """#include <stddef.h>
struct inner { char tag; long v; };
typedef struct state { int n; struct inner in; unsigned char *window; } state;
unsigned get(state *s) { return s->window[s->n]; }
"""
    (tmp_path / "func.c").write_text(src)
    t = Target(
        name="get",
        function="get",
        category="project",
        args=["u64"],
        ret="u32",
        loop_free=True,
        source="x.c:1",
        license="project",
        c_source=src,
        path=tmp_path,
        oracle="project",
    )
    out = record_layouts(t)
    assert "struct state, 32 bytes" in out
    assert "    24  unsigned char * window" in out
    assert "struct inner, 16 bytes" in out
    assert "     8  long v" not in out.split("struct state")[1]  # nested fields stay in their own type
