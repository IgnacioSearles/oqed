#!/bin/sh
# The trick-library experiment: the same held-out functions searched without
# and with verified wins in the prompt, twice each, contributor Spark for both
# tiers. Run ids go to experiments/tricks/runs.txt; analyze.py reads them.
set -e
cd "$(dirname "$0")/../.."
FNS="ceil_log2_u32 stbi_high_bit is_pow2_u32 isolate_lowest_u32 ctz_u32 popcount_swar_u32 gray_decode_u32 bitreverse_u32 sat_add_s32 avg_ceil_u32 abs32 clamp_u8"
for rep in 1 2; do
  for k in 0 3; do
    id=$(uv run oqed search $FNS --reasoning spark-contributor --tricks $k --max-usd 3 \
      2>&1 | tee -a experiments/tricks/search.log | sed -n 's#^run log: runs/\(.*\)\.jsonl#\1#p')
    echo "$k $rep $id" >> experiments/tricks/runs.txt
  done
done
