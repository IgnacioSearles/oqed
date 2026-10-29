# LevelDB's encoding code (util/coding.cc, hash.cc, bloom.cc in C) on a batch of writes.
#   make bench              build and run (clang)
#   make bench CC=gcc
#   oqed scan / optimize    with oqed.toml
ifeq ($(origin CC),default)
CC = clang
endif
CFLAGS ?= -O3 -march=x86-64-v3

SRC = oqed_support/bench.c oqed_support/buf.c coding.c hash.c
HDR = coding.h

bench: ldb_bench
	./ldb_bench

ldb_bench: $(SRC) $(HDR)
	$(CC) $(CFLAGS) -o $@ $(SRC)

clean:
	rm -f ldb_bench
