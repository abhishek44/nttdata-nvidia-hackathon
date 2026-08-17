#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 /path/to/stock-aiq-config.yml [/path/to/patched-config.yml]" >&2
  exit 2
fi

SOURCE_CONFIG="$1"
if [[ ! -f "$SOURCE_CONFIG" ]]; then
  echo "Source config does not exist: $SOURCE_CONFIG" >&2
  exit 2
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# -eq 2 ]]; then
  OUTPUT_CONFIG="$2"
else
  BASE="${SOURCE_CONFIG%.*}"
  EXT="${SOURCE_CONFIG##*.}"
  OUTPUT_CONFIG="${BASE}_recallzero.${EXT}"
fi

python -m pip install -e "$PROJECT_ROOT"
python -m recallzero.aiq.blueprint_patch "$SOURCE_CONFIG" --output "$OUTPUT_CONFIG"

echo
echo "RecallZero plugin installed into: $(python -c 'import sys; print(sys.executable)')"
echo "Patched config written to: $OUTPUT_CONFIG"
echo "Review the diff, set RECALLZERO_DATA_DIR to an absolute path, and launch your stock AI-Q command with the patched config."
