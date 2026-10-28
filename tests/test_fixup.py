from oqed import fixup


def test_errors_come_with_their_line_and_rule():
    errs = fixup.errors("\tmov eax, edi\n\tadd r10, edx\n\tlea rsi, [r9+rcx+rsi]\n\tret\n")
    assert [(e.line, e.text) for e in errs] == [(2, "add r10, edx"), (3, "lea rsi, [r9+rcx+rsi]")]
    assert "same width" in errs[0].describe() and "at most two" in errs[1].describe()
    assert fixup.errors("\tmov eax, edi\n\tret\n") == []


def test_only_unambiguous_errors_are_fixed():
    body = (
        "\tshl rax, rcx\n\tbzhi rax, rbx, r11d\n\tshrx ecx, ecx, rax\n\tmov word ptr [rdx], eax\n"
        "\tadd r10, edx\n\tmov rcx, dword ptr [rdi]\n\tret\n"
    )
    fixed, notes = fixup.fix(body)
    assert fixed.splitlines()[:4] == [
        "\tshl rax, cl",
        "\tbzhi rax, rbx, r11",
        "\tshrx ecx, ecx, eax",
        "\tmov word ptr [rdx], ax",
    ]
    # Zero- or sign-extend? A load of which width? Left to the model.
    assert "\tadd r10, edx" in fixed and "\tmov rcx, dword ptr [rdi]" in fixed
    assert len(notes) == 4
    assert fixup.fix("\tmov eax, edi\n\tret\n") == ("\tmov eax, edi\n\tret\n", [])
