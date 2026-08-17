#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
INSTALL_AIQ="${INSTALL_AIQ:-1}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN=python3
  else
    echo "Python 3.11-3.13 is required." >&2
    exit 1
  fi
fi

EXTRAS="dev"
if [[ "$INSTALL_AIQ" == "1" ]]; then
  EXTRAS="dev,aiq"
fi

if command -v uv >/dev/null 2>&1; then
  uv venv --python "$PYTHON_BIN" --seed .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  uv pip install -e ".[${EXTRAS}]"
else
  "$PYTHON_BIN" -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python -m pip install --upgrade pip setuptools wheel
  python -m pip install -e ".[${EXTRAS}]"
fi

cp -n .env.example .env || true

echo
echo "RecallZero environment created at $PROJECT_ROOT/.venv"
echo "Next:"
echo "  source .venv/bin/activate"
echo "  edit .env and set NVIDIA_API_KEY (or configure a local NIM base URL)"
echo "  recallzero doctor --probe-nim"
echo "  recallzero demo"
if [[ "$INSTALL_AIQ" == "1" ]]; then
  echo "  ./scripts/run_aiq.sh 'Investigate the 2021 and 2022 Ford Mustang Mach-E'"
fi
