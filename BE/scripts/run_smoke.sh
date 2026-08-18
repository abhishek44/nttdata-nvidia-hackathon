#!/usr/bin/env bash
set -euo pipefail
pytest -q
python -m recallzero.cli demo-backtest
