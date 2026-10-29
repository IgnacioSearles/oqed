"""Where a candidate crashed the project's test.

The test runs with a small library preloaded that, on SIGSEGV, SIGBUS, SIGILL
or SIGFPE, appends the program, its load address, the faulting instruction's
address, the fault address and the registers to $OQED_CRASH_FILE, then dies of
the same signal. `describe` turns that into the function, offset and
instruction that crashed, which says far more to the model than "Segmentation
fault".
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

REPORTER_C = r"""
#define _GNU_SOURCE
#include <fcntl.h>
#include <link.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ucontext.h>
#include <unistd.h>

static unsigned long bias;

static int first(struct dl_phdr_info *info, size_t size, void *data) {
    (void)size; (void)data;
    bias = info->dlpi_addr;
    return 1;  /* the program itself comes first */
}

static void report(int sig, siginfo_t *si, void *ctx) {
    const char *path = getenv("OQED_CRASH_FILE");
    if (path) {
        int fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0644);
        if (fd >= 0) {
            char exe[512] = {0}, buf[1536];
            ssize_t n = readlink("/proc/self/exe", exe, sizeof exe - 1);
            if (n < 0) exe[0] = 0;
            greg_t *r = ((ucontext_t *)ctx)->uc_mcontext.gregs;
            int len = snprintf(buf, sizeof buf,
                "sig=%d exe=%s bias=%lx rip=%llx addr=%lx rax=%llx rbx=%llx rcx=%llx rdx=%llx "
                "rsi=%llx rdi=%llx rbp=%llx rsp=%llx r8=%llx r9=%llx r10=%llx r11=%llx "
                "r12=%llx r13=%llx r14=%llx r15=%llx\n",
                sig, exe, bias, r[REG_RIP], (unsigned long)si->si_addr, r[REG_RAX], r[REG_RBX],
                r[REG_RCX], r[REG_RDX], r[REG_RSI], r[REG_RDI], r[REG_RBP], r[REG_RSP], r[REG_R8],
                r[REG_R9], r[REG_R10], r[REG_R11], r[REG_R12], r[REG_R13], r[REG_R14], r[REG_R15]);
            if (len > 0) write(fd, buf, len < (int)sizeof buf ? len : (int)sizeof buf - 1);
            close(fd);
        }
    }
    signal(sig, SIG_DFL);
    raise(sig);
}

__attribute__((constructor)) static void install(void) {
    dl_iterate_phdr(first, NULL);
    static char stack[65536];  /* a crash from a broken rsp needs its own stack */
    stack_t ss = {.ss_sp = stack, .ss_size = sizeof stack};
    sigaltstack(&ss, NULL);
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = report;
    sa.sa_flags = SA_SIGINFO | SA_ONSTACK;
    int sigs[] = {SIGSEGV, SIGBUS, SIGILL, SIGFPE};
    for (int i = 0; i < 4; i++) sigaction(sigs[i], &sa, NULL);
}
"""

SIGNALS = {4: "SIGILL", 7: "SIGBUS", 8: "SIGFPE", 11: "SIGSEGV"}
_REGS = "rax rbx rcx rdx rsi rdi rbp rsp r8 r9 r10 r11 r12 r13 r14 r15".split()


def reporter(cache: Path) -> Path | None:
    """The reporter library, built once per version of its source."""
    sha = hashlib.sha256(REPORTER_C.encode()).hexdigest()[:16]
    lib = cache / f"crash-{sha}.so"
    if lib.exists():
        return lib
    cache.mkdir(parents=True, exist_ok=True)
    src = cache / f"crash-{sha}.c"
    src.write_text(REPORTER_C)
    tmp = lib.with_suffix(".tmp")
    proc = subprocess.run(
        ["cc", "-O2", "-shared", "-fPIC", "-o", str(tmp), str(src)], capture_output=True, check=False
    )
    if proc.returncode != 0:
        return None
    tmp.replace(lib)  # atomic: parallel builds may race to it
    return lib


def describe(crash_file: Path) -> str | None:
    """The first crash in the file, as the function, offset and instruction
    that crashed plus the registers; None if nothing crashed."""
    try:
        line = crash_file.read_text().splitlines()[0]
    except (OSError, IndexError):
        return None
    f = dict(kv.split("=", 1) for kv in line.split() if "=" in kv)
    try:
        sig, rip, bias = int(f["sig"]), int(f["rip"], 16), int(f["bias"], 16)
    except (KeyError, ValueError):
        return None
    where, ins = _locate(f.get("exe", ""), rip - bias)
    regs = ", ".join(f"{r}={int(f[r], 16):#x}" for r in _REGS if r in f)
    head = f"{SIGNALS.get(sig, f'signal {sig}')} at {where}"
    if ins:
        head += f": `{ins}`"
    if sig in (7, 11):
        head += f" (fault address {int(f.get('addr', '0'), 16):#x})"
    return f"{head}; registers {regs}"


def _locate(exe: str, offset: int) -> tuple[str, str]:
    """The symbol+offset and the instruction at an offset into the program."""
    if not exe or not Path(exe).exists():
        return f"{offset:#x}", ""
    syms = subprocess.run(["nm", "-n", "--defined-only", exe], capture_output=True, text=True, check=False)
    where = f"{offset:#x}"
    for ln in syms.stdout.splitlines():
        parts = ln.split()
        if len(parts) != 3 or parts[1].lower() not in "tw":
            continue
        addr = int(parts[0], 16)
        if addr > offset:
            break
        where = f"{parts[2]}+{offset - addr:#x}"
    dis = subprocess.run(
        [
            "objdump",
            "-d",
            "-M",
            "intel",
            "--no-show-raw-insn",
            f"--start-address={offset:#x}",
            f"--stop-address={offset + 16:#x}",
            exe,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    for ln in dis.stdout.splitlines():
        if m := re.match(rf"\s*{offset:x}:\s+(.*)", ln):
            return where, " ".join(m.group(1).split("#")[0].split())
    return where, ""
