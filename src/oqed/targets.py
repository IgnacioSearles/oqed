"""Benchmark target definitions: `benchmarks/<name>/func.c` + `meta.toml`."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

IntType = Literal["i8", "u8", "i16", "u16", "i32", "u32", "i64", "u64"]

BENCHMARKS_DIR = Path(__file__).resolve().parents[2] / "benchmarks"

# System V AMD64 integer argument registers, in order.
ARG_REGS = ["rdi", "rsi", "rdx", "rcx", "r8", "r9"]
MAX_ARGS = 4


def bit_width(t: IntType) -> int:
    return int(t[1:])


def is_signed(t: IntType) -> bool:
    return t[0] == "i"


class Target(BaseModel):
    name: str
    function: str
    category: str
    args: list[IntType]
    ret: IntType
    loop_free: bool
    source: str
    license: str
    c_source: str
    path: Path
    reference_asm: str | None = None

    @property
    def c_file(self) -> Path:
        return self.path / "func.c"


def load_target(name_or_path: str | Path) -> Target:
    path = Path(name_or_path)
    if not path.is_dir():
        path = BENCHMARKS_DIR / str(name_or_path)
    meta = tomllib.loads((path / "meta.toml").read_text())
    if len(meta["args"]) > MAX_ARGS:
        raise ValueError(f"{path.name}: at most {MAX_ARGS} register arguments supported")
    ref = path / "reference.S"
    return Target(
        name=path.name,
        c_source=(path / "func.c").read_text(),
        path=path,
        reference_asm=ref.read_text() if ref.exists() else None,
        **meta,
    )


def load_all() -> list[Target]:
    return [load_target(p) for p in sorted(BENCHMARKS_DIR.iterdir()) if (p / "meta.toml").exists()]


_C_TYPES = {f"{s}{w}": f"{'' if s == 'i' else 'u'}int{w}_t" for s in "iu" for w in (8, 16, 32, 64)}


def c_signature(target: Target) -> str:
    args = ", ".join(f"{_C_TYPES[t]} {chr(ord('a') + i)}" for i, t in enumerate(target.args))
    return f"{_C_TYPES[target.ret]} {target.function}({args})"
