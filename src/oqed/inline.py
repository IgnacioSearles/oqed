"""Inlined benchmark mode: a candidate as GNU inline asm inside real loops.

Timing a function through `call`/`ret` (`harness.measure`) adds ~4.7 TSC ticks
per call and hides how the code behaves once a compiler inlines it. Here the
candidate becomes an `asm` statement in a static inline function, and a
generated C program times it against the target's C source, compiled by the
same compiler, in two loops over the same inputs:

- dependent: each result feeds the next call's first argument (latency),
- independent: results go to an array (throughput). The compiler may
  vectorize the C version of this loop but never the asm one, and the report
  says when it did.

Before timing, the program checks that C and asm agree on every test input.
The asm statement binds argument registers with constraints (`D`, `S`, `d`,
`c`), takes the result from `a`, and clobbers exactly the registers the
instructions write (iced-x86 lists implicit writes such as `mul` -> `rdx`).
Code that touches memory or the stack is not inlinable: an asm block must not
write below `rsp`, where the enclosing function may keep its red zone.

`reverify` compiles a wrapper around the asm statement and proves the
compiled bytes equivalent to the reference, so a proof covers the operand
glue the compiler adds, not just the candidate.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import iced_x86 as iced
from pydantic import BaseModel

from . import harness, verify
from .events import Cycles
from .targets import ARG_REGS, IntType, Target, bit_width, is_signed
from .toolchain import CFLAGS, Compiler, ToolError, assemble, compile_to_asm, extract_function

CONSTRAINT = {"rdi": "D", "rsi": "S", "rdx": "d", "rcx": "c"}
_OTHER: dict[Compiler, Compiler] = {"clang": "gcc", "gcc": "clang"}

_C_TYPES = {f"{s}{w}": f"{'' if s == 'i' else 'u'}int{w}_t" for s in "iu" for w in (8, 16, 32, 64)}
_REG_NAMES = {
    v: k.lower() for k, v in vars(iced.Register).items() if isinstance(v, int) and not k.startswith("_")
}
_A = iced.OpAccess
_WRITES = {_A.WRITE, _A.COND_WRITE, _A.READ_WRITE, _A.READ_COND_WRITE}
_LABEL_DEF = re.compile(r"^\s*([.\w$]+):\s*$")

VARIANTS = ("dep_ref", "dep_cand", "indep_ref", "indep_cand", "scalar_ref")
CHECK_INPUTS = 8000
SAMPLES = 101


class NotInlinable(ValueError):
    pass


class Mismatch(ToolError):
    """The inlined asm computed something different from the C in the loop
    program (an operand-binding bug, or a candidate that only works when called)."""


def written_registers(code: bytes) -> set[str]:
    """Full-width names of the registers `code` writes (gp: `rcx`, vector: `zmm1`),
    implicit operands included, `rip` and flags excluded. Raises NotInlinable
    for memory or stack accesses."""
    factory = iced.InstructionInfoFactory()
    out: set[str] = set()
    for insn in iced.Decoder(64, code):
        if insn.mnemonic == iced.Mnemonic.RET:
            continue
        info = factory.info(insn)
        regs = [(iced.RegisterExt.full_register(r.register), r.access) for r in info.used_registers()]
        if any(full == iced.Register.RSP for full, _ in regs):
            raise NotInlinable(f"uses the stack pointer ({insn})")
        if info.used_memory():
            raise NotInlinable(f"accesses memory ({insn})")
        out |= {_REG_NAMES[full] for full, acc in regs if full != iced.Register.RIP and acc in _WRITES}
    return out


def _template(body: str) -> str:
    """The body as an asm template: comments dropped, labels made unique per
    asm instance with `%=`, every `ret` turned into a jump to the end."""
    lines = [ln.split("#")[0].split("//")[0].rstrip() for ln in body.splitlines()]
    lines = [ln.strip() for ln in lines if ln.strip()]
    if any(c in ln for ln in lines for c in "{|}%"):
        raise NotInlinable("contains characters that are special in asm templates ({ | } %)")
    # Numeric local labels (`1:` with `jz 1f`) may repeat, so they work in
    # inline asm as they are; only named labels need renaming.
    labels = {m.group(1) for ln in lines if (m := _LABEL_DEF.match(ln)) and not m.group(1).isdigit()}
    if labels:
        alts = "|".join(re.escape(lb) for lb in sorted(labels, key=len, reverse=True))
        pat = re.compile(rf"(?<![\w.$])({alts})(?![\w$])")
        lines = [pat.sub(lambda m: f"{m.group(1)}_%=", ln) for ln in lines]
    while lines and lines[-1] == "ret":
        lines.pop()
    end_used = any(ln == "ret" for ln in lines)
    lines = ["jmp .Loqed_end_%=" if ln == "ret" else ln for ln in lines]
    if end_used:
        lines.append(".Loqed_end_%=:")
    return "".join(f"\t{ln}\\n" for ln in lines)


def _arg_c_type(t: IntType) -> str:
    """Width the asm sees an argument at: narrow types extended to 32 bits, as
    the System V convention compilers rely on (and the verifier assumes)."""
    if bit_width(t) == 64:
        return _C_TYPES[t]
    return "int32_t" if is_signed(t) else "uint32_t"


def inline_function(body: str, target: Target, name: str) -> str:
    """C source of `static inline RET name(args)` running `body` as inline asm
    (Intel syntax: compile with `-masm=intel`)."""
    if len(target.args) > len(CONSTRAINT):
        raise NotInlinable(f"more than {len(CONSTRAINT)} register arguments")
    written = written_registers(assemble(body))
    names = [chr(ord("a") + i) for i in range(len(target.args))]
    params = ", ".join(f"{_C_TYPES[t]} {n}" for t, n in zip(target.args, names, strict=True))
    decls = [f"    {_arg_c_type(t)} v{n} = {n};" for t, n in zip(target.args, names, strict=True)]
    outs, ins = ['"=a"(r)'], []
    for reg, n in zip(ARG_REGS, names, strict=False):
        (outs if reg in written else ins).append(f'"{"+" if reg in written else ""}{CONSTRAINT[reg]}"(v{n})')
    bound = {"rax", *ARG_REGS[: len(names)]}
    clobbers = [f'"{r}"' for r in sorted(written - bound) if not r.startswith(("xmm", "ymm", "zmm"))]
    clobbers += [f'"xmm{r[3:]}"' for r in sorted(written) if r.startswith(("xmm", "ymm", "zmm"))]
    clobbers.append('"cc"')
    return (
        f"static inline __attribute__((always_inline)) {_C_TYPES[target.ret]} {name}({params}) {{\n"
        + "\n".join(decls)
        + "\n    uint64_t r;\n"
        + f'    __asm__("{_template(body)}"\n'
        + f"            : {', '.join(outs)}\n"
        + f"            : {', '.join(ins)}\n"
        + f"            : {', '.join(clobbers)});\n"
        + f"    return ({_C_TYPES[target.ret]})r;\n}}\n"
    )


def _array(name: str, t: IntType, values: list[int]) -> str:
    def lit(v: int) -> str:
        if v == -(1 << 63):
            return "(-9223372036854775807ll - 1)"
        return f"{v}{'ull' if bit_width(t) == 64 and not is_signed(t) else 'll'}"

    rows = ",".join(lit(v) for v in values)
    return f"{_C_TYPES[t]} {name}[{len(values)}] = {{{rows}}};\n"


def _typed(regs: list[int], target: Target) -> tuple[int, ...]:
    return tuple(harness.ret_value(r, t) for r, t in zip(regs, target.args, strict=True))


def bench_program(body: str, target: Target) -> str:
    """A self-contained C program: correctness check, then timings as JSON lines.

    `BARRIER` (an empty asm with a memory clobber) between passes and around
    the timestamps stops the compiler from merging passes or hoisting the
    loops out of the timed region; `passes` is read from a volatile for the
    same reason."""
    idx = range(len(target.args))
    check = harness.make_inputs(target)[:CHECK_INPUTS]
    bench = [_typed(r, target) for r in harness.bench_inputs(target)]
    ret_t, a0 = _C_TYPES[target.ret], _C_TYPES[target.args[0]]

    def call(f: str, arr: str, first: str | None = None) -> str:
        return f"{f}({', '.join(first if i == 0 and first else f'{arr}{i}[i]' for i in idx)})"

    data = "".join(_array(f"chk{i}", target.args[i], [v[i] for v in check]) for i in idx)
    data += "".join(_array(f"in{i}", target.args[i], [v[i] for v in bench]) for i in idx)
    loops = ""
    for f in ("ref", "cand"):
        loops += f"""
__attribute__((noinline)) static {ret_t} dep_{f}(int passes) {{
    {ret_t} x = 0;
    for (int p = 0; p < passes; p++) {{
        for (int i = 0; i < N; i++) x = {call(f, "in", f"({a0})(in0[i] ^ ({a0})x)")};
        BARRIER;
    }}
    return x;
}}
__attribute__((noinline)) static void indep_{f}(int passes) {{
    for (int p = 0; p < passes; p++) {{
        for (int i = 0; i < N; i++) out[i] = {call(f, "in")};
        BARRIER;
    }}
}}
"""
    loops += f"""
__attribute__((noinline)) static void scalar_ref(int passes) {{
    for (int p = 0; p < passes; p++) {{
        NOVEC for (int i = 0; i < N; i++) out[i] = {call("ref", "in")};
        BARRIER;
    }}
}}
"""
    src = target.c_source.replace(f"{target.function}(", "ref(")
    return f"""#define _GNU_SOURCE
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <x86intrin.h>

#define BARRIER __asm__ volatile("" ::: "memory")
#ifdef __clang__
#define NOVEC _Pragma("clang loop vectorize(disable) interleave(disable)")
#else
#define NOVEC _Pragma("GCC novector")
#endif

{src}
{inline_function(body, target, "cand")}
{data}
#define N {len(bench)}
#define NCHK {len(check)}
#define SAMPLES {SAMPLES}
{ret_t} out[N];
static volatile {ret_t} sink;
static volatile int passes = 8;
{loops}
static uint64_t ticks(void) {{
    BARRIER;
    _mm_lfence();
    uint64_t t = __rdtsc();
    _mm_lfence();
    BARRIER;
    return t;
}}

static void run(int v) {{
    switch (v) {{
    case 0: sink = dep_ref(passes); break;
    case 1: sink = dep_cand(passes); break;
    case 2: indep_ref(passes); break;
    case 3: indep_cand(passes); break;
    case 4: scalar_ref(passes); break;
    }}
}}

static int cmp(const void *a, const void *b) {{
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}}

int main(int argc, char **argv) {{
    if (argc > 1) {{
        cpu_set_t set;
        CPU_ZERO(&set);
        CPU_SET(atoi(argv[1]), &set);
        sched_setaffinity(0, sizeof set, &set);
    }}
    for (int i = 0; i < NCHK; i++) {{
        {ret_t} want = {call("ref", "chk")}, got = {call("cand", "chk")};
        if (want != got) {{
            printf("{{\\"mismatch\\": %d, \\"want\\": %lld, \\"got\\": %lld}}\\n",
                   i, (long long)want, (long long)got);
            return 2;
        }}
    }}
    enum {{ V = 5 }};
    static double s[V][SAMPLES];
    const char *names[V] = {{"dep_ref", "dep_cand", "indep_ref", "indep_cand", "scalar_ref"}};
    for (int k = 0; k < 3 * V; k++) run(k % V);
    for (int r = 0; r < SAMPLES; r++)
        for (int v = 0; v < V; v++) {{
            uint64_t t0 = ticks();
            run(v);
            s[v][r] = (double)(ticks() - t0) / ((double)passes * N);
        }}
    for (int v = 0; v < V; v++) {{
        qsort(s[v], SAMPLES, sizeof(double), cmp);
        printf("{{\\"%s\\": [%.4f, %.4f, %.4f]}}\\n",
               names[v], s[v][SAMPLES / 4], s[v][SAMPLES / 2], s[v][3 * SAMPLES / 4]);
    }}
    return 0;
}}
"""


def _vectorize_flags(compiler: Compiler) -> list[str]:
    if compiler == "clang":
        return ["-Rpass=loop-vectorize|slp-vectorizer"]
    return ["-fopt-info-vec-optimized"]


class InlineResult(BaseModel):
    compiler: Compiler
    dep_ref: Cycles  # TSC ticks per element
    dep_cand: Cycles
    indep_ref: Cycles
    indep_cand: Cycles
    scalar_ref: Cycles  # the C independent loop with vectorization disabled
    ref_vectorized: bool  # the compiler vectorized the C independent loop

    @property
    def latency_speedup(self) -> float:
        return self.dep_ref.median / self.dep_cand.median

    @property
    def throughput_speedup(self) -> float:
        return self.indep_ref.median / self.indep_cand.median

    @property
    def scalar_throughput_speedup(self) -> float:
        """Against C that is not vectorized: what the asm is worth in loops the
        compiler can't vectorize anyway (loop-carried dependencies, calls)."""
        return self.scalar_ref.median / self.indep_cand.median


@dataclass
class Built:
    """A compiled benchmark program, ready to run (see `build`)."""

    compiler: Compiler
    dir: Path
    ref_vectorized: bool

    @property
    def exe(self) -> Path:
        return self.dir / "bench"


def build(body: str, target: Target, compiler: Compiler, march: str) -> Built:
    """Compile the inlined benchmark. Separate from `run` so compiling can
    happen in parallel while timing runs are serialized."""
    d = Path(tempfile.mkdtemp(prefix="oqed-inline-"))
    try:
        src = d / "bench.c"
        src.write_text(bench_program(body, target))
        cmd = [compiler, "-O3", f"-march={march}", "-masm=intel", *_vectorize_flags(compiler)]
        cmd += ["-o", str(d / "bench"), str(src)]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise ToolError(f"{compiler} failed on the inlined benchmark:\n{proc.stderr.strip()}")
        return Built(compiler, d, _loop_vectorized(proc.stderr + proc.stdout, src.read_text()))
    except BaseException:
        shutil.rmtree(d, ignore_errors=True)
        raise


def run(built: Built) -> InlineResult:
    """Run a built benchmark (pinned to `OQED_BENCH_CPU` if set) and delete it."""
    try:
        cmd = [str(built.exe)]
        if (cpu := harness.bench_cpu()) is not None:
            cmd.append(str(cpu))
        res = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120)
    finally:
        shutil.rmtree(built.dir, ignore_errors=True)
    rows = [json.loads(ln) for ln in res.stdout.splitlines() if ln.strip()]
    if res.returncode == 2:
        raise Mismatch(f"inlined candidate disagrees with the C source: {rows[0]}")
    if res.returncode != 0:
        raise ToolError(f"inlined benchmark failed ({res.returncode}): {res.stderr.strip()}")
    t = {k: Cycles(median=round(v[1], 3), spread=round(v[2] - v[0], 3)) for r in rows for k, v in r.items()}
    return InlineResult(compiler=built.compiler, ref_vectorized=built.ref_vectorized, **t)


def measure(body: str, target: Target, compiler: Compiler, march: str) -> InlineResult:
    """Build and run the inlined benchmark with one compiler."""
    return run(build(body, target, compiler, march))


def _loop_vectorized(remarks: str, src: str) -> bool:
    """Whether a vectorization remark points into the C independent loop."""
    lines = src.splitlines()
    start = next(i for i, ln in enumerate(lines) if "indep_ref(" in ln and "noinline" in ln) + 1
    span = range(start, start + 4)
    for m in re.finditer(r"bench\.c:(\d+):\d+: (?:remark|optimized)", remarks):
        if int(m.group(1)) in span and "vectoriz" in remarks[m.end() : m.end() + 200]:
            return True
    return False


def reverify(body: str, target: Target, compiler: Compiler, march: str) -> verify.VerifyResult:
    """Compile a wrapper around the inline asm with `compiler` and prove its
    bytes equivalent to the target's C. The reference is the C compiled by the
    same compiler, or by the other one if the verifier can't take that (gcc
    sometimes lays out blocks with a backward jump)."""
    wrapper = "#include <stdint.h>\n" + inline_function(body, target, "cand")
    args = ", ".join(f"{_C_TYPES[t]} {chr(ord('a') + i)}" for i, t in enumerate(target.args))
    names = ", ".join(chr(ord("a") + i) for i in range(len(target.args)))
    wrapper += f"{_C_TYPES[target.ret]} {target.function}({args}) {{ return cand({names}); }}\n"
    with tempfile.NamedTemporaryFile("w", suffix=".c", delete=False) as f:
        f.write(wrapper)
    try:
        cmd = [compiler, *CFLAGS, f"-march={march}", "-S", "-o", "-", f.name]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise ToolError(f"{compiler} failed on the wrapper:\n{proc.stderr.strip()}")
    finally:
        os.unlink(f.name)
    compiled = assemble(extract_function(proc.stdout, target.function))
    refs = [assemble(compile_to_asm(target, c, march)) for c in (compiler, _OTHER[compiler])]
    ref = next((r for r in refs if verify.unsupported(r) is None), refs[0])
    return verify.check_sync(target, ref, compiled)
