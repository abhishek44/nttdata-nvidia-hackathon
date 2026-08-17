# RecallZero v2 Release Validation

Release version: **0.3.2**

## Validated in the build environment

- `pytest -q -ra`: **47 tests passed, 1 optional NAT-runtime test skipped** because `nat` is not installed in the build container.
- `python -m compileall -q src tests`: passed.
- `python -m recallzero.cli --version`: command entry point loaded and reported version 0.3.2.
- `python -m recallzero.cli demo`: synthetic end-to-end analysis and Time Machine replay completed.
- Python wheel built successfully without network build isolation and was inspected for package files, dashboard assets, CLI and plugin entry points.
- Wheel SHA-256: `0945247a7f7671ee11b22263a345c2b45cfb83b7cf93601f5bc6725b0bdc128c`.

## Covered by automated tests

- Complaint and recall normalization.
- NHTSA complaint response parsing, campaign-number lookup, recall-catalog model resolution, endpoint-specific date parsing, and non-transient retry behavior.
- Heuristic failure extraction and multi-component selection.
- NIM/local-endpoint configuration detection.
- NIM guided-JSON request payloads, thinking-disabled structured extraction, fenced-JSON recovery, and empty-content diagnostics.
- Retry handling for transient NVIDIA errors, including HTTP 429 followed by success, and no retry for permanent HTTP 400 errors.
- Transient hosted-NIM failures are not silently converted into heuristic signatures by default.
- Incremental signature checkpointing and cache upgrade from heuristic signatures to a configured NIM model.
- Dual-axis failure mechanism + consequence-family representation before child clustering.
- Cross-component meta-signal aggregation with child-cluster and ODI lineage preservation.
- Complete-link cosine refinement guarded by maximum sampled pairwise dispersion to prevent DBSCAN density-chain over-merging.
- Raw failure-mode, failure-mechanism and consequence-family purity plus component-group diagnostics, distance summaries, noise and dominant-cluster checks.
- Deterministic event-scoped severity provenance: hypothetical/negated/background motion text is rejected, parked events do not get motion credit from unrelated driving references, and structured NHTSA crash/injury flags are authoritative.
- Explicit campaign-reference sanity matching and structured recall matching for high-voltage power-loss patterns.
- Deterministic trend, active-week/max-consecutive persistence, evidence and risk behavior.
- Recall Time Machine exclusion of post-recall records and withholding of alerts from semantically degraded snapshots.
- Target-recall leakage invalidation and evidence-level cutoff checks.
- Distinction between `EARLY_ALERT_TARGET_UNMATCHED` and true `NO_EARLY_SIGNAL`, plus evaluation-only post-hoc top-candidate target scores after snapshot freeze.
- Evidence critic checks including component-family alignment and generic large-cluster labels.
- FastAPI health, dashboard and offline demo routes.
- AI-Q/NAT configuration patching and NAT tool schemas using module-level Pydantic input types.

## Requires validation on the target GB10 environment

The build environment does not contain the user's NVIDIA credentials or installed NAT runtime. Therefore these checks must be run on GB10:

1. Reinstall 0.3.2 while preserving `.env` and `data/cache`.
2. Re-run the full 2021-2022 Mustang Mach-E analysis and inspect canonical defect-family groups, cluster sizes, raw/canonical purity and per-cluster distance summaries.
3. Re-run the pre-recall cutoff analysis for 2022-06-09.
4. Re-run campaign `22V412000` and compare `first_any_alert_date` with `first_matching_alert_date`.
5. Verify native NAT tool-calling execution with the installed runtime.
6. Repeat across additional historical positive cases and negative controls before calibrating thresholds.

## Non-claims

- The synthetic demo is an installation smoke test, not a real safety result.
- A high deterministic risk score is an investigation ranking, not proof that a safety defect exists.
- A pre-recall alert does not count as a target-recall prediction unless post-hoc matching qualifies it without leakage.
- Campaign `22V412000` remains an experiment candidate until the 0.3.2 replay is reviewed.
- Prototype clustering, risk and recall-match thresholds must be calibrated on multiple positive and negative cases before production use.
