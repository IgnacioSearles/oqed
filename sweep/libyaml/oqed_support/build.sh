#!/bin/sh
set -e
rm -rf oqed_build
mkdir -p oqed_build oqed_build/include
cat > oqed_build/include/config.h <<'EOF'
#define HAVE_STDLIB_H 1
#define HAVE_STRING_H 1
#define HAVE_STRINGS_H 1
#define YAML_VERSION_MAJOR 0
#define YAML_VERSION_MINOR 2
#define YAML_VERSION_PATCH 5
#define YAML_VERSION_STRING "0.2.5"
EOF
CFLAGS="-O3 -march=x86-64-v3 -DHAVE_CONFIG_H -DYAML_DECLARE_STATIC -Iinclude -Ioqed_build/include"
SRCS="src/api.c src/dumper.c src/emitter.c src/loader.c src/parser.c src/reader.c src/scanner.c src/writer.c"
for s in $SRCS; do
  o="oqed_build/$(basename $s .c).o"
  $CC $CFLAGS -c "$s" -o "$o" &
done
wait
ar rcs oqed_build/libyaml.a oqed_build/*.o
$CC -O3 -march=x86-64-v3 -Iinclude -Ioqed_build/include -o oqed_driver oqed_support/driver.c oqed_build/libyaml.a
python3 oqed_support/gen.py > oqed_input.yaml
