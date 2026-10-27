//! Assembly entry points for calling candidate code.
//!
//! `oqed_call` calls a candidate once under the System V ABI and reports
//! whether it preserved the callee-saved registers and the stack pointer.
//! `oqed_time` calls it in a tight loop between serialized `rdtsc` reads,
//! cycling through an array of inputs so branchy code can't win by having one
//! perfectly predicted input.

use std::arch::global_asm;

/// Arguments passed in rdi, rsi, rdx, rcx.
pub type Args = [u64; 4];

/// Written by `oqed_call` after the candidate returns.
#[repr(C)]
#[derive(Debug, Default, Clone, Copy)]
pub struct CallOut {
    pub rax: u64,
    /// Bit set per violation, see `CLOBBER_NAMES`.
    pub clobbered: u64,
}

/// Bit order of `CallOut::clobbered`.
pub const CLOBBER_NAMES: [&str; 7] = ["rbx", "rbp", "r12", "r13", "r14", "r15", "rsp"];

unsafe extern "C" {
    pub fn oqed_call(f: *const u8, args: *const Args, out: *mut CallOut);
    /// Returns elapsed TSC ticks for `n` calls, cycling through `count`
    /// argument tuples. With `latency != 0` each call's first argument is
    /// `args[i].0 + (previous rax & 0)`, so calls form a dependency chain
    /// (with a fixed overhead of `and` + `add` per call) while still seeing
    /// the input values unchanged.
    pub fn oqed_time(f: *const u8, args: *const Args, count: u64, n: u64, latency: u64) -> u64;
}

// Canary values loaded into callee-saved registers before the call. Argument
// registers the candidate should not read (rax, r8, r9) get junk too, so code
// that depends on them is likely to fail differential testing.
global_asm!(
    r#"
    .pushsection .bss
    .p2align 3
.Loqed_saved_rsp:
    .zero 8
    .popsection

    .text
    .globl oqed_call
    .hidden oqed_call
    .p2align 4
oqed_call:
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
    push rdx
    mov [rip + .Loqed_saved_rsp], rsp
    mov r11, rdi
    mov r10, rsi
    movabs rbx, 0x5a17c0de0000b0b0
    movabs rbp, 0x5a17c0de0001b0b1
    movabs r12, 0x5a17c0de0012b012
    movabs r13, 0x5a17c0de0013b013
    movabs r14, 0x5a17c0de0014b014
    movabs r15, 0x5a17c0de0015b015
    movabs rax, 0xdeadbeefcafef00d
    movabs r8, 0x8badf00d8badf00d
    movabs r9, 0x0ddba11c0ffee000
    mov rdi, [r10]
    mov rsi, [r10 + 8]
    mov rdx, [r10 + 16]
    mov rcx, [r10 + 24]
    call r11
    xor r10d, r10d
    cmp rsp, [rip + .Loqed_saved_rsp]
    je 1f
    or r10, 64
    mov rsp, [rip + .Loqed_saved_rsp]
1:
    movabs r11, 0x5a17c0de0000b0b0
    cmp rbx, r11
    je 1f
    or r10, 1
1:
    movabs r11, 0x5a17c0de0001b0b1
    cmp rbp, r11
    je 1f
    or r10, 2
1:
    movabs r11, 0x5a17c0de0012b012
    cmp r12, r11
    je 1f
    or r10, 4
1:
    movabs r11, 0x5a17c0de0013b013
    cmp r13, r11
    je 1f
    or r10, 8
1:
    movabs r11, 0x5a17c0de0014b014
    cmp r14, r11
    je 1f
    or r10, 16
1:
    movabs r11, 0x5a17c0de0015b015
    cmp r15, r11
    je 1f
    or r10, 32
1:
    pop rdx
    mov [rdx], rax
    mov [rdx + 8], r10
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret

    .globl oqed_time
    .hidden oqed_time
    .p2align 4
oqed_time:
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
    sub rsp, 8
    mov r12, rdi
    mov [rsp], rsi
    mov r14, rsi
    shl rdx, 5
    lea r15, [rsi + rdx]
    mov r13, rcx
    mov rbp, r8
    lfence
    rdtsc
    shl rdx, 32
    or rax, rdx
    mov rbx, rax
    lfence
    xor eax, eax
    test rbp, rbp
    jnz 3f
2:
    mov rdi, [r14]
    mov rsi, [r14 + 8]
    mov rdx, [r14 + 16]
    mov rcx, [r14 + 24]
    call r12
    add r14, 32
    cmp r14, r15
    jne 1f
    mov r14, [rsp]
1:
    dec r13
    jnz 2b
    jmp 4f
3:
    and rax, 0
    add rax, [r14]
    mov rdi, rax
    mov rsi, [r14 + 8]
    mov rdx, [r14 + 16]
    mov rcx, [r14 + 24]
    call r12
    add r14, 32
    cmp r14, r15
    jne 1f
    mov r14, [rsp]
1:
    dec r13
    jnz 3b
4:
    lfence
    rdtsc
    shl rdx, 32
    or rax, rdx
    sub rax, rbx
    add rsp, 8
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret
"#
);
