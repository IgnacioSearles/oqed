//! oqed harness: runs and times candidate x86-64 machine code in a sandboxed
//! child process. This is the only crate in oqed with `unsafe` code.

mod exec;
mod trampoline;

use pyo3::prelude::*;

#[pymodule]
mod oqed_harness {
    use std::time::Duration;

    use pyo3::exceptions::{PyOSError, PyValueError};
    use pyo3::prelude::*;

    use crate::exec::{self, Error, Status};
    use crate::trampoline::{Args, CLOBBER_NAMES};

    fn to_py_err(e: Error) -> PyErr {
        match e {
            Error::EmptyCode => PyValueError::new_err("empty machine code"),
            Error::NoInputs => PyValueError::new_err("no inputs"),
            Error::Sys(e) => PyOSError::new_err(e.to_string()),
            Error::Io => PyOSError::new_err("failed to map executable memory"),
        }
    }

    fn status_str(s: Status) -> &'static str {
        match s {
            Status::Ok => "ok",
            Status::Crashed => "crashed",
            Status::TimedOut => "timeout",
        }
    }

    #[pyclass(frozen, get_all)]
    pub struct ExecResult {
        /// "ok", "crashed" or "timeout".
        status: &'static str,
        signal: Option<i32>,
        /// Inputs that ran to completion. On a crash, the crashing input's index.
        completed: usize,
        /// rax after each completed call.
        outputs: Vec<u64>,
        /// Per completed call: names of callee-saved registers (and rsp) that
        /// were not preserved. Empty lists when the ABI was respected.
        clobbered: Vec<Vec<&'static str>>,
    }

    #[pyclass(frozen, get_all)]
    pub struct BenchResult {
        status: &'static str,
        signal: Option<i32>,
        /// TSC ticks per call, one per repetition.
        samples: Vec<f64>,
    }

    fn to_args(inputs: &[Vec<u64>]) -> PyResult<Vec<Args>> {
        inputs
            .iter()
            .map(|v| {
                if v.len() > 4 {
                    return Err(PyValueError::new_err("at most 4 arguments are supported"));
                }
                let mut a = [0u64; 4];
                a[..v.len()].copy_from_slice(v);
                Ok(a)
            })
            .collect()
    }

    /// Call `code` once per input. `inputs` is a list of argument tuples
    /// (up to 4 unsigned 64-bit values, passed in rdi, rsi, rdx, rcx).
    #[pyfunction]
    #[pyo3(signature = (code, inputs, timeout_ms = 1000))]
    fn execute(
        py: Python<'_>,
        code: Vec<u8>,
        inputs: Vec<Vec<u64>>,
        timeout_ms: u64,
    ) -> PyResult<ExecResult> {
        let args = to_args(&inputs)?;
        let out = py
            .detach(|| exec::execute(&code, &args, Duration::from_millis(timeout_ms)))
            .map_err(to_py_err)?;
        Ok(ExecResult {
            status: status_str(out.status),
            signal: out.signal,
            completed: out.completed,
            outputs: out.outputs.iter().map(|o| o.rax).collect(),
            clobbered: out
                .outputs
                .iter()
                .map(|o| {
                    CLOBBER_NAMES
                        .iter()
                        .enumerate()
                        .filter(|(bit, _)| o.clobbered & (1 << bit) != 0)
                        .map(|(_, name)| *name)
                        .collect()
                })
                .collect(),
        })
    }

    /// Time `code`, cycling through the argument tuples in `inputs`. Each
    /// sample is the mean TSC ticks per call over `iters` back-to-back calls.
    /// With `latency=True` each call depends on the previous call's result
    /// (dependent chain); otherwise calls are independent (throughput).
    #[pyfunction]
    #[pyo3(signature = (code, inputs, iters = 1000, reps = 101, warmup = 10, latency = false, cpu = None, timeout_ms = 5000))]
    #[allow(clippy::too_many_arguments)]
    fn bench(
        py: Python<'_>,
        code: Vec<u8>,
        inputs: Vec<Vec<u64>>,
        iters: u64,
        reps: usize,
        warmup: usize,
        latency: bool,
        cpu: Option<usize>,
        timeout_ms: u64,
    ) -> PyResult<BenchResult> {
        let args = to_args(&inputs)?;
        let out = py
            .detach(|| {
                exec::bench(
                    &code,
                    &args,
                    iters,
                    reps,
                    warmup,
                    latency,
                    cpu,
                    Duration::from_millis(timeout_ms),
                )
            })
            .map_err(to_py_err)?;
        Ok(BenchResult {
            status: status_str(out.status),
            signal: out.signal,
            samples: out.samples,
        })
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use crate::exec::{Status, bench, execute};

    const T: Duration = Duration::from_millis(500);
    const IDENTITY: &[u8] = &[0x89, 0xf8, 0xc3]; // mov eax, edi; ret
    const ADD: &[u8] = &[0x8d, 0x04, 0x37, 0xc3]; // lea eax, [rdi + rsi]; ret

    #[test]
    fn runs_and_returns_rax() {
        let out = execute(ADD, &[[2, 3, 0, 0], [u32::MAX as u64, 1, 0, 0]], T).unwrap();
        assert_eq!(out.status, Status::Ok);
        assert_eq!(
            out.outputs.iter().map(|o| o.rax).collect::<Vec<_>>(),
            [5, 0]
        );
        assert!(out.outputs.iter().all(|o| o.clobbered == 0));
    }

    #[test]
    fn illegal_instruction_crashes_child_only() {
        let out = execute(&[0x0f, 0x0b], &[[0; 4], [1, 0, 0, 0]], T).unwrap(); // ud2
        assert_eq!(out.status, Status::Crashed);
        assert_eq!(out.signal, Some(libc::SIGILL));
        assert_eq!(out.completed, 0);
    }

    #[test]
    fn segfault_reports_failing_input() {
        // test rdi, rdi; jz ok; mov rax, [0]; ok: ret
        let code = [
            0x48, 0x85, 0xff, 0x74, 0x08, 0x48, 0x8b, 0x04, 0x25, 0, 0, 0, 0, 0xc3,
        ];
        let out = execute(&code, &[[0; 4], [0; 4], [7, 0, 0, 0], [0; 4]], T).unwrap();
        assert_eq!(out.status, Status::Crashed);
        assert_eq!(out.signal, Some(libc::SIGSEGV));
        assert_eq!(out.completed, 2);
    }

    #[test]
    fn infinite_loop_times_out() {
        let out = execute(&[0xeb, 0xfe], &[[0; 4]], Duration::from_millis(50)).unwrap(); // jmp $
        assert_eq!(out.status, Status::TimedOut);
    }

    #[test]
    fn detects_clobbered_callee_saved_register() {
        let out = execute(&[0x31, 0xdb, 0xc3], &[[0; 4]], T).unwrap(); // xor ebx, ebx; ret
        assert_eq!(out.status, Status::Ok);
        assert_eq!(out.outputs[0].clobbered, 1); // rbx
        let out = execute(&[0x4d, 0x31, 0xff, 0xc3], &[[0; 4]], T).unwrap(); // xor r15, r15; ret
        assert_eq!(out.outputs[0].clobbered, 32); // r15
    }

    #[test]
    fn detects_unbalanced_stack() {
        // pop rcx; sub rsp, 8; push rcx; ret  -> returns with rsp 8 bytes low
        let code = [0x59, 0x48, 0x83, 0xec, 0x08, 0x51, 0xc3];
        let out = execute(&code, &[[0; 4]], T).unwrap();
        assert_eq!(out.status, Status::Ok);
        assert_eq!(out.outputs[0].clobbered, 64);
    }

    #[test]
    fn bench_produces_samples() {
        let out = bench(
            IDENTITY,
            &[[1, 0, 0, 0], [2, 0, 0, 0]],
            1000,
            21,
            3,
            true,
            None,
            Duration::from_secs(5),
        )
        .unwrap();
        assert_eq!(out.status, Status::Ok);
        assert_eq!(out.samples.len(), 21);
        assert!(out.samples.iter().all(|&s| s > 0.0 && s < 1000.0));
    }

    #[test]
    fn bench_crash_is_reported() {
        let out = bench(&[0x0f, 0x0b], &[[0; 4]], 10, 5, 0, false, None, T).unwrap();
        assert_eq!(out.status, Status::Crashed);
        assert!(out.samples.is_empty());
    }
}
