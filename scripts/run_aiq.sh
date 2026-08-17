#!/usr/bin/env bash
set -euo pipefail

QUESTION="${*:-Investigate emerging safety concerns for the 2021 and 2022 Ford Mustang Mach-E.}"

if command -v nat >/dev/null 2>&1; then
  exec nat run --config_file configs/aiq/recallzero_agent.yml --input "$QUESTION"
elif command -v aiq >/dev/null 2>&1; then
  exec aiq run --config_file configs/aiq/legacy_recallzero_agent.yml --input "$QUESTION"
else
  echo "Neither the nat nor aiq CLI is installed. Run: uv pip install -e '.[aiq]'" >&2
  exit 1
fi
