/* Call counter linked into programs built by oqed-cc in record mode.
 *
 * Every function is compiled with -finstrument-functions, which calls
 * __cyg_profile_func_enter with the function's address on entry. clang and gcc
 * insert the call before inlining, so inlined calls are counted too. At exit
 * the counts are appended to $OQED_PROFILE together with the address of
 * __oqed_prof_anchor, which `oqed scan` uses to undo address randomization
 * before looking the addresses up with nm.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#define NOI __attribute__((no_instrument_function))
#define SLOTS (1 << 16)

static uintptr_t keys[SLOTS];
static uint64_t counts[SLOTS];

NOI __attribute__((noinline, used)) void __oqed_prof_anchor(void) {}

NOI void __cyg_profile_func_enter(void *fn, void *site)
{
	(void) site;
	uintptr_t k = (uintptr_t) fn;
	size_t i = (size_t) (((uint64_t) k >> 4) * 0x9e3779b97f4a7c15ull >> 48);
	for (size_t n = 0; n < SLOTS; n++, i = (i + 1) & (SLOTS - 1)) {
		uintptr_t cur = __atomic_load_n(&keys[i], __ATOMIC_RELAXED);
		if (cur == 0) {
			uintptr_t expected = 0;
			if (__atomic_compare_exchange_n(&keys[i], &expected, k, 0, __ATOMIC_RELAXED,
							__ATOMIC_RELAXED))
				cur = k;
			else
				cur = expected;
		}
		if (cur == k) {
			__atomic_fetch_add(&counts[i], 1, __ATOMIC_RELAXED);
			return;
		}
	}
}

NOI void __cyg_profile_func_exit(void *fn, void *site)
{
	(void) fn;
	(void) site;
}

NOI __attribute__((destructor)) static void oqed_prof_dump(void)
{
	const char *path = getenv("OQED_PROFILE");
	if (!path)
		return;
	FILE *f = fopen(path, "a");
	if (!f)
		return;
	char exe[4096];
	ssize_t n = readlink("/proc/self/exe", exe, sizeof exe - 1);
	exe[n > 0 ? n : 0] = 0;
	fprintf(f, "exe %s\nanchor %llx\n", exe, (unsigned long long) (uintptr_t) &__oqed_prof_anchor);
	for (size_t i = 0; i < SLOTS; i++)
		if (keys[i])
			fprintf(f, "%llx %llu\n", (unsigned long long) keys[i], (unsigned long long) counts[i]);
	fclose(f);
}
