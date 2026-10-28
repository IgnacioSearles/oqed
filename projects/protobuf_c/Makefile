# Real-world testbed for oqed: protobuf-c serializing a batch of telemetry points.
#   make bench              build and run (clang)
#   make bench CC=gcc
#   make compare            plain build vs oqed-cc build (oqed.lock): packed bytes must match
#   oqed scan / optimize    build pbc_profile through oqed-cc's record mode (oqed.toml)
ifeq ($(origin CC),default)
CC = clang
endif
OQED_CC ?= uv run oqed-cc
CFLAGS ?= -O3 -march=x86-64-v3

SRC = bench.c protobuf-c.c telemetry.pb-c.c
HDR = protobuf-c.h telemetry.pb-c.h

bench: pbc_bench
	./pbc_bench

pbc_bench: $(SRC) $(HDR)
	$(CC) $(CFLAGS) -o $@ $(SRC)

pbc_bench_oqed: $(SRC) $(HDR) oqed.lock
	$(OQED_CC) --cc=$(CC) $(CFLAGS) -o $@ $(SRC)

pbc_profile: $(SRC) $(HDR)
	$(CC) $(CFLAGS) -o $@ $(SRC)

compare: pbc_bench pbc_bench_oqed
	@for i in 1 2 3; do ./pbc_bench | sed 's/^/plain    /'; ./pbc_bench_oqed | sed 's/^/oqed-cc  /'; done
	@test "$$(./pbc_bench | awk '{print $$NF}')" = "$$(./pbc_bench_oqed | awk '{print $$NF}')" \
	      && echo "packed bytes identical"

clean:
	rm -f pbc_bench pbc_bench_oqed pbc_profile

.PHONY: bench compare clean
