//! Run candidate machine code in a forked child process.
//!
//! The parent maps the code executable and preallocates a shared result
//! buffer, then forks. The child only calls into the trampoline and writes
//! results into shared memory (no allocation after fork, so this is safe in a
//! multithreaded host process). A crash, illegal instruction or hang kills
//! only the child; the parent reports it.

use std::num::NonZeroUsize;
use std::ptr::NonNull;
use std::time::{Duration, Instant};

use memmap2::{Mmap, MmapMut};
use nix::sched::{CpuSet, sched_setaffinity};
use nix::sys::mman::{MapFlags, ProtFlags, mmap_anonymous, munmap};
use nix::sys::resource::{Resource, setrlimit};
use nix::sys::signal::{SigHandler, Signal, kill, signal};
use nix::sys::wait::{WaitPidFlag, WaitStatus, waitpid};
use nix::unistd::{ForkResult, Pid, fork};

use crate::trampoline::{Args, CallOut, oqed_call, oqed_time};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Ok,
    Crashed,
    TimedOut,
}

#[derive(Debug)]
pub struct ExecOutcome {
    pub status: Status,
    /// Signal that killed the child, if it crashed.
    pub signal: Option<i32>,
    /// Number of inputs that ran to completion; on a crash, the index of the
    /// input that crashed.
    pub completed: usize,
    pub outputs: Vec<CallOut>,
}

#[derive(Debug)]
pub struct BenchOutcome {
    pub status: Status,
    pub signal: Option<i32>,
    /// TSC ticks per call, one entry per repetition (after warmup).
    pub samples: Vec<f64>,
}

#[derive(Debug, Clone, Copy)]
pub enum Error {
    EmptyCode,
    Sys(nix::Error),
    Io,
}

impl From<nix::Error> for Error {
    fn from(e: nix::Error) -> Self {
        Error::Sys(e)
    }
}

/// Executable copy of the candidate code.
fn map_code(code: &[u8]) -> Result<Mmap, Error> {
    if code.is_empty() {
        return Err(Error::EmptyCode);
    }
    let mut m = MmapMut::map_anon(code.len()).map_err(|_| Error::Io)?;
    m.copy_from_slice(code);
    m.make_exec().map_err(|_| Error::Io)
}

/// Anonymous memory shared between parent and child.
struct Shared {
    ptr: NonNull<u8>,
    len: usize,
}

impl Shared {
    fn new(len: usize) -> Result<Self, Error> {
        let len = len.max(8);
        // SAFETY: fresh anonymous mapping, no existing memory is aliased.
        let ptr = unsafe {
            mmap_anonymous(
                None,
                NonZeroUsize::new(len).unwrap(),
                ProtFlags::PROT_READ | ProtFlags::PROT_WRITE,
                MapFlags::MAP_SHARED,
            )?
        };
        Ok(Shared {
            ptr: ptr.cast(),
            len,
        })
    }

    fn as_mut<T>(&self) -> *mut T {
        self.ptr.as_ptr().cast()
    }
}

impl Drop for Shared {
    fn drop(&mut self) {
        // SAFETY: we own the mapping and nothing borrows it past this point.
        unsafe {
            let _ = munmap(self.ptr.cast(), self.len);
        }
    }
}

/// Fork, run `child` in the child, and wait up to `timeout`.
fn run_in_child(timeout: Duration, child: impl FnOnce()) -> Result<(Status, Option<i32>), Error> {
    // SAFETY: the child only runs `child` (which must not allocate) and exits.
    match unsafe { fork()? } {
        ForkResult::Child => {
            prepare_child();
            child();
            // SAFETY: `_exit` skips atexit handlers inherited from the host process.
            unsafe { libc::_exit(0) }
        }
        ForkResult::Parent { child } => wait_child(child, timeout),
    }
}

/// Restore default fault handling (the host may have installed handlers,
/// e.g. Python's faulthandler) and disable core dumps.
fn prepare_child() {
    for sig in [
        Signal::SIGSEGV,
        Signal::SIGBUS,
        Signal::SIGILL,
        Signal::SIGFPE,
        Signal::SIGTRAP,
    ] {
        // SAFETY: resetting to the default action installs no handler code.
        unsafe {
            let _ = signal(sig, SigHandler::SigDfl);
        }
    }
    let _ = setrlimit(Resource::RLIMIT_CORE, 0, 0);
}

fn wait_child(pid: Pid, timeout: Duration) -> Result<(Status, Option<i32>), Error> {
    let deadline = Instant::now() + timeout;
    loop {
        match waitpid(pid, Some(WaitPidFlag::WNOHANG))? {
            WaitStatus::StillAlive => {
                if Instant::now() >= deadline {
                    let _ = kill(pid, Signal::SIGKILL);
                    let _ = waitpid(pid, None);
                    return Ok((Status::TimedOut, None));
                }
                std::thread::sleep(Duration::from_micros(100));
            }
            WaitStatus::Exited(_, 0) => return Ok((Status::Ok, None)),
            WaitStatus::Exited(_, _) => return Ok((Status::Crashed, None)),
            WaitStatus::Signaled(_, sig, _) => return Ok((Status::Crashed, Some(sig as i32))),
            _ => continue,
        }
    }
}

/// Call `code` once per argument tuple.
pub fn execute(code: &[u8], inputs: &[Args], timeout: Duration) -> Result<ExecOutcome, Error> {
    let code = map_code(code)?;
    // Layout: [completed: u64][CallOut; n]
    let shared = Shared::new(8 + inputs.len() * size_of::<CallOut>())?;
    let completed: *mut u64 = shared.as_mut();
    // SAFETY: the buffer holds a u64 header followed by `inputs.len()` CallOuts.
    let outs: *mut CallOut = unsafe { shared.as_mut::<u8>().add(8).cast() };

    let (status, signal) = run_in_child(timeout, || {
        for (i, args) in inputs.iter().enumerate() {
            // SAFETY: `code` is mapped executable; the trampoline checks the ABI
            // contract, and any fault only kills this child.
            unsafe {
                oqed_call(code.as_ptr(), args, outs.add(i));
                completed.write_volatile(i as u64 + 1);
            }
        }
    })?;

    // SAFETY: the child has exited; the parent is the only user of `shared`.
    let done = (unsafe { completed.read_volatile() } as usize).min(inputs.len());
    let outputs = (0..done).map(|i| unsafe { outs.add(i).read() }).collect();
    Ok(ExecOutcome {
        status,
        signal,
        completed: done,
        outputs,
    })
}

/// Time `code` on one argument tuple: `warmup + reps` repetitions of
/// `iters` calls each, optionally pinned to one CPU.
#[allow(clippy::too_many_arguments)]
pub fn bench(
    code: &[u8],
    args: Args,
    iters: u64,
    reps: usize,
    warmup: usize,
    latency: bool,
    cpu: Option<usize>,
    timeout: Duration,
) -> Result<BenchOutcome, Error> {
    let code = map_code(code)?;
    let shared = Shared::new(reps * size_of::<f64>())?;
    let samples: *mut f64 = shared.as_mut();
    let iters = iters.max(1);
    let mut cpuset = CpuSet::new();
    if let Some(c) = cpu {
        cpuset.set(c)?;
    }

    let (status, signal) = run_in_child(timeout, || {
        if cpu.is_some() {
            let _ = sched_setaffinity(Pid::from_raw(0), &cpuset);
        }
        for r in 0..warmup + reps {
            // SAFETY: as in `execute`; only called on code that passed testing.
            let ticks = unsafe { oqed_time(code.as_ptr(), &args, iters, latency as u64) };
            if r >= warmup {
                unsafe { samples.add(r - warmup).write(ticks as f64 / iters as f64) };
            }
        }
    })?;

    let samples = if status == Status::Ok {
        // SAFETY: the child has exited and wrote all `reps` samples.
        (0..reps)
            .map(|i| unsafe { samples.add(i).read() })
            .collect()
    } else {
        Vec::new()
    };
    Ok(BenchOutcome {
        status,
        signal,
        samples,
    })
}
