#!/usr/bin/env bash
set -euo pipefail

HOST="${RECALLZERO_DEMO_UI_HOST:-0.0.0.0}"
PORT="${RECALLZERO_DEMO_UI_PORT:-8501}"
export RECALLZERO_API_URL="${RECALLZERO_API_URL:-http://127.0.0.1:8080}"

exec streamlit run demo/app.py \
  --server.address "${HOST}" \
  --server.port "${PORT}" \
  --server.headless true \
  --browser.gatherUsageStats false
