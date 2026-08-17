#!/usr/bin/env bash
set -euo pipefail

recallzero backtest \
  --make FORD \
  --model "MUSTANG MACH-E" \
  --years 2021,2022 \
  --campaign 22V412000 \
  --recall-date 2022-06-10 \
  --json data/runs/mach_e_22V412000_result.json \
  "$@"
