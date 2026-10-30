"""The programs oqed runs (compilers, binutils, llvm-mca), and how to install
the missing ones. `uv sync` only covers the Python side and the Rust harness."""

from __future__ import annotations

import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class Tool:
    name: str
    why: str
    pkg: dict[str, str]  # package manager -> package
    required: bool = True


def _same(pkg: str) -> dict[str, str]:
    return dict.fromkeys(("apt", "dnf", "pacman", "zypper"), pkg)


TOOLS = [
    Tool("clang", "the reference build, and projects by default", _same("clang")),
    Tool("gcc", "the second baseline", _same("gcc")),
    Tool("as", "assembles candidates", _same("binutils")),
    Tool("nm", "finds functions in a project's binary", _same("binutils")),
    Tool("objdump", "says where a crashing test crashed", _same("binutils")),
    Tool("llvm-mca", "static cycle estimates", _same("llvm")),
    Tool("clang++", "C++ projects", _same("clang"), required=False),
    Tool("g++", "C++ projects", {**_same("gcc-c++"), "apt": "g++", "pacman": "gcc"}, required=False),
    Tool("git", "optimizing a project straight from a repo URL", _same("git"), required=False),
]

_INSTALL = {
    "apt": "sudo apt install",
    "dnf": "sudo dnf install",
    "pacman": "sudo pacman -S",
    "zypper": "sudo zypper install",
}
_MANAGER_BIN = {"apt": "apt-get", "dnf": "dnf", "pacman": "pacman", "zypper": "zypper"}


def missing(required_only: bool = True) -> list[Tool]:
    return [t for t in TOOLS if (t.required or not required_only) and not shutil.which(t.name)]


def install_hint(tools: list[Tool]) -> str:
    manager = next((m for m, b in _MANAGER_BIN.items() if shutil.which(b)), None)
    if manager is None:
        return "install them with your package manager: " + ", ".join(t.name for t in tools)
    pkgs = list(dict.fromkeys(t.pkg[manager] for t in tools))
    return f"{_INSTALL[manager]} {' '.join(pkgs)}"


def report(tools: list[Tool]) -> str:
    width = max(len(t.name) for t in tools)
    lines = [f"  {t.name:<{width}}  {t.why}" for t in tools]
    return "\n".join(lines) + f"\n\n  {install_hint(tools)}"
