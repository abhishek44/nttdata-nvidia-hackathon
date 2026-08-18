#!/usr/bin/env python
from __future__ import annotations

import argparse
import json

from recallzero.backtest.candidate_runner import run_candidate


def main():
    parser = argparse.ArgumentParser(description="Run a real NHTSA RecallZero historical candidate backtest")
    parser.add_argument("candidate", help="Path to candidate JSON, e.g. data/candidates/22V412000.json")
    parser.add_argument("--out", default="backtest_result.json")
    args = parser.parse_args()
    result = run_candidate(args.candidate)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
