# RecallZero v2 Release Validation

Release version: **0.3.5.post2** — pre-validation local-NIM structured-extraction reliability patch.

## Scope

This release is intentionally narrow. The validation cohort had not produced a scientifically valid all-case result because hosted extraction was rate-limited and the first local-NIM smoke run returned several HTTP-success responses with `failure_mode: null`. Those responses failed Pydantic validation and interactive RecallZero used heuristic fallback, producing `semantic_quality=MIXED`. The preregistered Detector v1 benchmark requires `minimum_nim_fraction: 1.0`, so such runs are invalid by design.

0.3.5.post2 changes only the semantic extraction contract/recovery path and benchmark fail-closed behavior before the validation lock is recreated. It does **not** change severity, taxonomy, clustering, meta-signal construction, trend/risk math, recall matching, threshold, weights, windows, or Time Machine anti-leakage logic. The post1 NHTSA complaint adapter remains unchanged.

The freeze manifest now records:

- `benchmark_release: 0.3.5.post2`
- `input_adapter_revision: nhtsa-complaint-catalog-v2`
- `semantic_extraction_revision: local-nim-structured-contract-v2`

Because extraction semantics are detector input, the extractor source hash is refreshed before validation locking. Any old benchmark lock whose freeze-manifest hash references post1 must be discarded and recreated only after the post2 smoke/preflight gate succeeds.

## Structured extraction correction

- The Pydantic-generated guided JSON schema now exposes `minLength: 1` for both `system` and `failure_mode`.
- The extraction system prompt explicitly forbids null/empty `failure_mode`; if the complaint cannot support a more specific label, the allowed conservative value is `UNSPECIFIED FAILURE`.
- An invalid JSON/schema response receives exactly one deterministic NIM-only repair attempt with the original complaint, invalid response, and validation error.
- A successful repair remains `extraction_method=nim`.
- If the repair still fails, RecallZero raises `NIMStructuredExtractionError` instead of silently inventing a repaired signature.
- Interactive/non-benchmark analysis retains the historical heuristic fallback for engineering continuity.
- A validation benchmark with `minimum_nim_fraction: 1.0` forcibly disables both ordinary and transient heuristic fallback, so the case either remains all-NIM or is explicitly invalidated.

## Regression coverage

Tests cover the five ODIs observed in the local-NIM smoke failure (`11443132`, `11443932`, `11448380`, `11459714`, `11466251`), schema non-empty constraints, one successful repair, failed repair, strict benchmark fail-closed semantics, and preservation of interactive heuristic fallback.

## Required GB10 validation gate

Before recreating the validation benchmark lock, use a fresh/isolated failure-signature cache and rerun the 57-complaint Mach-E pre-recall smoke. The gate is:

- 57 eligible complaints
- 57 NIM signatures
- 0 heuristic signatures
- NIM fraction 1.0
- semantic quality ACCEPTABLE
- zero unrepaired structured-extraction failures

Only after that gate and refreshed benchmark preflight pass should a new validation lock be created.

## Build validation

- `PYTHONPATH=src pytest -q -ra`: **100 collected, 99 passed, 1 skipped, 0 failed**.
- The one skip is the optional NAT runtime integration because `nat` is not installed in the isolated build environment.
- `python -m compileall -q src tests`: **PASS**.
- `PYTHONPATH=src python -m recallzero.cli --version`: **RecallZero 0.3.5.post2**.
- `freeze-verify --manifest benchmarks/detector_freeze_v1.yaml`: **PASS**.
- Wheel build via local build toolchain (`pip wheel --no-build-isolation`) succeeded.
- Isolated-target wheel install reports **0.3.5.post2**.
- Detector-module comparison against 0.3.5.post1: **1 changed** (`intelligence/extractor.py`), all other frozen detector modules unchanged; `config/risk.yml` is byte-identical.
- Evaluation harness intentionally changes `benchmark.py` to enforce fail-closed semantics when the preregistered NIM fraction is 1.0.
- Wheel SHA-256: `bfc2dfa98b958a356fb7e8e5daec272d09fac69ba074fa50f41d42d9f7ff64b9`.
