#!/usr/bin/env bash
set -euo pipefail

HOST="${RECALLZERO_DEMO_BACKEND_HOST:-0.0.0.0}"
PORT="${RECALLZERO_DEMO_BACKEND_PORT:-8080}"

exec uvicorn recallzero.api.app:app --host "${HOST}" --port "${PORT}"
