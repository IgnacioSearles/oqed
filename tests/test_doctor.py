from oqed import doctor


def test_missing_tools_come_with_one_install_command(monkeypatch):
    have = {"apt-get", "clang"}
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/usr/bin/{name}" if name in have else None)
    gone = doctor.missing()
    assert [t.name for t in gone] == ["gcc", "as", "nm", "objdump", "llvm-mca"]
    assert doctor.install_hint(gone) == "sudo apt install gcc binutils llvm"


def test_nothing_missing(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert doctor.missing(required_only=False) == []
