#!/bin/sh
# Muse Spark Open (Baseten) on both tiers, on the 12 held-out functions of the
# trick-library experiment, with the library on (--tricks 3, the default), so
# the tricks=3 arms of experiments/tricks are the contributor Spark comparison.
set -e
cd "$(dirname "$0")/../.."
FNS="ceil_log2_u32 stbi_high_bit is_pow2_u32 isolate_lowest_u32 ctz_u32 popcount_swar_u32 gray_decode_u32 bitreverse_u32 sat_add_s32 avg_ceil_u32 abs32 clamp_u8"
id=$(uv run oqed search $FNS --volume spark-open --reasoning spark-open --tricks 3 --max-usd 6 \
  2>&1 | tee -a experiments/spark-open/search.log | sed -n 's#^run log: runs/\(.*\)\.jsonl#\1#p')
echo "$id" >> experiments/spark-open/runs.txt
