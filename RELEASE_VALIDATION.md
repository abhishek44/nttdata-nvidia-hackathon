# RecallZero v2 Release Validation

Release version: **0.3.5.post4** — read-only Target Risk Audit diagnostic.

## Scope

0.3.5.post4 does not tune Detector v1. It adds one offline diagnostic command over an already completed raw benchmark artifact. The historical Detector v1 Validation Run 1 remains the 0.3.5.post2 failed-but-informative result; post4 must not be used to rewrite it.

The command is:

```bash
recallzero benchmark-target-risk-audit RAW_RESULT \
  --target-match-threshold 0.45 \
  --top-lineages 8 \
  --json target_risk_audit.json
```

## Diagnostic boundary

The Target Risk Audit:

- requires the source raw benchmark to report both `freeze_verified=true` and `lock_verified=true`;
- performs zero LLM calls;
- performs zero embedding calls;
- performs zero detector replays;
- performs zero clustering or risk recomputation;
- reads the frozen `risk_factors`, risk scores, target scores, evidence counts, alert flags, and lineage IDs already persisted in the raw benchmark;
- groups repeated snapshots by lineage so target attribution and maximum risk are analyzed independently on the same signal lineage;
- reports weighted factor headroom only as a diagnostic, not as a recommendation to tune weights or scores.

The raw benchmark persists the ordinary top-N candidates by risk plus every alert, not every detector signal. Therefore absence from the audit is not proof that no target-related signal existed.

## Freeze provenance

- Detector source logic is byte-identical to 0.3.5.post3.
- `src/recallzero/cli.py` changed only to expose the new read-only diagnostic command.
- `src/recallzero/benchmark_risk_audit.py` is new diagnostic-only code.
- `benchmarks/detector_freeze_v1.yaml` records the post4 diagnostic CLI/module hashes.
- Exact post2/post3 freeze manifests are preserved under `benchmarks/archive/` for historical provenance.

## Build validation

- `PYTHONPATH=src pytest -ra`: **108 collected, 107 passed, 1 skipped, 0 failed**.
- The one skip is the optional NAT runtime integration because `nat` is not installed in the isolated build environment.
- `python -m compileall -q src tests`: **PASS**.
- `PYTHONPATH=src python -m recallzero.cli --version`: **RecallZero 0.3.5.post4**.
- `freeze-verify --manifest benchmarks/detector_freeze_v1.yaml`: **PASS**.
- `benchmark-target-risk-audit --help`: **PASS**.
- CLI smoke against an available freeze/lock-verified raw benchmark artifact: **PASS**.
- Wheel build via `pip wheel --no-deps --no-build-isolation`: **PASS**.
- Isolated `--target` wheel import reports **0.3.5.post4** and imports `build_target_risk_audit`: **PASS**.
- Wheel SHA-256: `03e7ceddad19d9702273bc0fcbc20197d3da9a8bf4d23831150c8c3eaa735984`.
- Current post4 freeze manifest SHA-256: `b6348e5b59b96d3e9404f075ff8c33bddeb34483a74c03dd7215a1ef4198895b`.
- Archived post2 freeze SHA-256: `e3aaa9eef94aa30ea41770281d62792d585c4415499e8e6ca87b81e7f918ade9`.
- Archived post3 freeze SHA-256: `a6a6fed0016d0a079af492e65310322d3f8d3c426e4d293d93dd326657dd7807`.
