# RecallZero v2 Release Validation

Release version: **0.3.4** — Detector Freeze v1 benchmark/evaluation infrastructure.

## Detector freeze guarantee

The detector-critical modules listed in `benchmarks/detector_freeze_v1.yaml` were compared byte-for-byte (SHA-256) with the 0.3.3a2 source tree. The following remained unchanged: runtime config model, NHTSA client/normalizer/repository, NIM client/extractor/embeddings, taxonomy, clustering, severity, trend engine, risk engine, pipeline/meta construction, recall matcher, and utility/lineage helpers. `config/risk.yml` is also unchanged.

0.3.4 intentionally changes only evaluation/reporting surfaces: benchmark/freeze tooling, additive `BacktestCandidate.risk_factors` persistence, CLI commands, manifest metadata, documentation, and tests.

## Validated in the build environment

- `PYTHONPATH=src pytest -q -ra`: **66 tests passed, 1 optional NAT-runtime test skipped** because `nat` is not installed in the build container.
- `python -m compileall -q src tests`: passed.
- `python -m recallzero.cli --version`: reported `RecallZero 0.3.4`.
- `python -m recallzero.cli demo`: synthetic end-to-end analysis and Time Machine replay completed.
- `recallzero freeze-verify --manifest benchmarks/detector_freeze_v1.yaml`: all detector module hashes, evaluation module hashes, live risk/clustering/trend/model/execution settings, and active `risk.yml` hash passed.
- Positive and targetless-negative benchmark orchestration smoke tests completed against the offline synthetic records; top-candidate outputs contained all five risk-factor breakdowns.
- Python wheel built successfully without network build isolation and installed into an isolated target path; the installed package reported `0.3.4` and exposed `BacktestCandidate.risk_factors`.
- Wheel SHA-256: `2a4456a906a3988a515eb36e860bf5f373b1b26efd9422cd828cf68d60d5ec02`.

## New 0.3.4 evaluation coverage

- `benchmarks/detector_freeze_v1.yaml` explicitly pins `analytics/severity.py`, `analytics/risk_engine.py`, `analytics/trend_engine.py`, `intelligence/taxonomy.py`, `intelligence/clustering.py`, `pipeline.py`, `recall/matcher.py`, and the rest of the data-to-alert path.
- Freeze verification reads the active `Settings().risk_config()` and active risk file rather than trusting copied YAML numbers.
- Every Time Machine `BacktestCandidate` persists `severity`, `trend`, `persistence`, `evidence`, and `recall_gap` score/weight/contribution/explanation fields.
- `config/candidates.yml` is now directly consumable by `recallzero benchmark` and supports development/holdout plus positive/targetless-negative roles.
- Negative controls replay the same frozen detector to an exclusive evaluation boundary without a target recall.
- Aggregate outputs include positive sensitivity/lead-time and negative false-alert snapshot/lineage burden.
- `benchmarks/KNOWN_GAPS.md` records TAX-001 and META-001 as benchmark-driven deferred issues.

## Requires validation on the target GB10 environment

1. Install 0.3.4 while preserving `.env`, normalized evidence, and signature cache.
2. Run `recallzero freeze-verify --manifest benchmarks/detector_freeze_v1.yaml` before any benchmark.
3. Extend `config/candidates.yml` with unrelated historical positive cases and targetless controls; keep Mach-E 22V412000 in the **development** split.
4. Run `recallzero benchmark --manifest config/candidates.yml --freeze benchmarks/detector_freeze_v1.yaml`.
5. Do not modify detector modules, `risk.yml`, thresholds, weights, taxonomy, meta eligibility, or severity logic while collecting Detector v1 benchmark data.
6. Inspect unique false-lineage spikes for TAX-001 lineage fragmentation before interpreting them as distinct false alarms.

## Non-claims

- The build environment did not execute live NHTSA/NVIDIA/NAT calls.
- Synthetic benchmark smoke results are not vehicle-safety findings.
- Mach-E 22V412000 is a development case, not an untouched holdout.
- 0.3.4 is not a detector-improvement release; it is a freeze and evaluation release.
