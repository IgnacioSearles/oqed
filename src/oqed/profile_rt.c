/* Sampling profiler linked into programs built by oqed-cc in record mode.
 *
 * A timer signal every 100 us records the instruction each thread was at.
 * `oqed scan` maps the addresses to functions: by symbol (the function the
 * code was compiled into, with whatever it inlined) and, through the debug
 * info (record builds add -g), by the inlined function the instruction came
 * from. Nothing is instrumented, so the program runs at its normal speed and
 * the shares are honest. (Counting calls with -finstrument-functions cost more
 * than lz4's own work: 64% of the samples landed in the counter.)
 *
 * The timer is a CLOCK_MONOTONIC POSIX timer because CPU-time itimers only
 * fire on the kernel tick (~100 samples a second); time spent blocked lands
 * outside the program's functions and only dilutes the shares. Each thread
 * gets its own timer (pthread_create is wrapped), since a process-wide timer
 * signal would mostly hit a main thread waiting for its workers.
 *
 * At exit the samples are appended to $OQED_PROFILE together with the address
 * of __oqed_prof_anchor, which `oqed scan` uses to undo address randomization.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/syscall.h>
#include <time.h>
#include <ucontext.h>
#include <unistd.h>

#define SLOTS (1 << 16)

static uintptr_t pcs[SLOTS];
static uint64_t samples[SLOTS];
static int sampling;

__attribute__((noinline, used)) void __oqed_prof_anchor(void) {}

/* Lock-free open-addressing count table; safe in a signal handler. */
static void bump(uintptr_t k)
{
	size_t i = (size_t) (((uint64_t) k >> 2) * 0x9e3779b97f4a7c15ull >> 48);
	for (size_t n = 0; n < SLOTS; n++, i = (i + 1) & (SLOTS - 1)) {
		uintptr_t cur = __atomic_load_n(&pcs[i], __ATOMIC_RELAXED);
		if (cur == 0) {
			uintptr_t expected = 0;
			if (__atomic_compare_exchange_n(&pcs[i], &expected, k, 0, __ATOMIC_RELAXED,
							__ATOMIC_RELAXED))
				cur = k;
			else
				cur = expected;
		}
		if (cur == k) {
			__atomic_fetch_add(&samples[i], 1, __ATOMIC_RELAXED);
			return;
		}
	}
}

static void on_sample(int sig, siginfo_t *info, void *ctx)
{
	(void) sig;
	(void) info;
	bump((uintptr_t) ((ucontext_t *) ctx)->uc_mcontext.gregs[REG_RIP]);
}

/* A timer that signals this thread every 100 us. */
static void start_thread_timer(void)
{
	if (!sampling)
		return;
	struct sigevent ev = { 0 };
	ev.sigev_notify = SIGEV_THREAD_ID;
	ev.sigev_signo = SIGPROF;
	ev._sigev_un._tid = (pid_t) syscall(SYS_gettid);
	timer_t t;
	if (timer_create(CLOCK_MONOTONIC, &ev, &t) == 0) {
		struct itimerspec every = { { 0, 100000 }, { 0, 100000 } };
		timer_settime(t, 0, &every, NULL);
	}
}

struct start {
	void *(*fn)(void *);
	void *arg;
};

static void *thread_main(void *p)
{
	struct start s = *(struct start *) p;
	free(p);
	/* Thread pools often block every signal in their workers (lz4 does);
	 * the sampler's handler only bumps a counter, so let it through. */
	sigset_t prof;
	sigemptyset(&prof);
	sigaddset(&prof, SIGPROF);
	pthread_sigmask(SIG_UNBLOCK, &prof, NULL);
	start_thread_timer();
	return s.fn(s.arg);
}

int pthread_create(pthread_t *th, const pthread_attr_t *attr, void *(*fn)(void *), void *arg)
{
	static int (*real)(pthread_t *, const pthread_attr_t *, void *(*)(void *), void *);
	if (!real)
		real = (int (*)(pthread_t *, const pthread_attr_t *, void *(*)(void *), void *)) dlsym(
			RTLD_NEXT, "pthread_create");
	struct start *s = malloc(sizeof *s);
	if (!s)
		return real(th, attr, fn, arg);
	s->fn = fn;
	s->arg = arg;
	int rc = real(th, attr, thread_main, s);
	if (rc)
		free(s);
	return rc;
}

__attribute__((constructor)) static void oqed_prof_start(void)
{
	if (!getenv("OQED_PROFILE"))
		return;
	struct sigaction sa = { 0 };
	sa.sa_sigaction = on_sample;
	sa.sa_flags = SA_SIGINFO | SA_RESTART;
	sigemptyset(&sa.sa_mask);
	sigaction(SIGPROF, &sa, NULL);
	sampling = 1;
	start_thread_timer();
}

__attribute__((destructor)) static void oqed_prof_dump(void)
{
	const char *path = getenv("OQED_PROFILE");
	if (!path)
		return;
	sampling = 0;
	signal(SIGPROF, SIG_IGN);
	FILE *f = fopen(path, "a");
	if (!f)
		return;
	char exe[4096];
	ssize_t n = readlink("/proc/self/exe", exe, sizeof exe - 1);
	exe[n > 0 ? n : 0] = 0;
	fprintf(f, "exe %s\nanchor %llx\n", exe, (unsigned long long) (uintptr_t) &__oqed_prof_anchor);
	for (size_t i = 0; i < SLOTS; i++)
		if (pcs[i])
			fprintf(f, "pc %llx %llu\n", (unsigned long long) pcs[i], (unsigned long long) samples[i]);
	fclose(f);
}
