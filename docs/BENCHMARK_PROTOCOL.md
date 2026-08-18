# RecallZero Detector Freeze v1 benchmark protocol

RecallZero 0.3.4 is an evaluation release. Detector behavior is frozen at the 0.3.3a2 state. Benchmark infrastructure may add persisted diagnostics or reporting fields, but must not change complaint eligibility, signature semantics, clustering/meta membership, severity validation, trend/persistence, recall-gap scoring, risk weights, alert threshold, or anti-leakage boundaries.

## Freeze verification

Before a benchmark run, execute:

```bash
recallzero freeze-verify --manifest benchmarks/detector_freeze_v1.yaml
```

The verifier checks:

- SHA-256 hashes for detector-critical source modules, including `pipeline.py` and `recall/matcher.py`.
- SHA-256 hashes for the evaluation schema/Time Machine/benchmark verifier modules.
- The active `risk.yml` file hash.
- Values loaded through the live `Settings().risk_config()` object.
- Live clustering, trend, extraction-consistency, and model settings.

A mismatch invalidates benchmark comparability unless the operator deliberately uses `--allow-freeze-mismatch`; such a run must not be mixed with Detector v1 benchmark results.

## Candidate manifest

`config/candidates.yml` is the benchmark manifest. Existing identity fields are preserved and evaluation metadata is additive:

```yaml
candidates:
  - name: Historical positive
    make: FORD
    model: MUSTANG MACH-E
    model_years: [2021, 2022]
    campaign_number: 22V412000
    official_recall_date: 2022-06-10
    expected_role: positive
    benchmark_split: development

  - name: Targetless control
    make: EXAMPLE
    model: MODEL
    model_years: [2022]
    campaign_number: null
    evaluation_end_date: 2023-01-01
    expected_role: negative
    benchmark_split: holdout
```

`expected_role`, target campaign metadata, and post-hoc target descriptions are evaluation-only. Positive target recall data remains hidden until a detector snapshot is frozen. Negative controls never receive a target recall.

## Persisted snapshot evidence

Each frozen top candidate stores the full deterministic risk-factor breakdown:

- severity: score, weight, contribution, explanation
- trend: score, weight, contribution, explanation
- persistence: score, weight, contribution, explanation
- evidence: score, weight, contribution, explanation
- recall gap: score, weight, contribution, explanation

This is required for causal comparison between detector versions. A final score change should be attributable to changed factors rather than inferred after the fact.

## Aggregate metrics

Positive cases report target-like recognition, first any alert, first qualified alert, lead time, alert persistence, and maximum pre-boundary risk. Negative controls report any alert, alerting snapshots, alert occurrences, unique alert lineages, and maximum risk.

Aggregate outputs include:

- positive sensitivity
- lead-time distribution and median lead time
- negative case alert rate
- false alert snapshots / vehicle replay year
- unique false lineages / vehicle replay year

The unique-lineage metric has a known interpretive dependency: TAX-001 can fragment one real-world pattern across multiple lineage IDs. Inspect lineage composition before treating an elevated unique-lineage count as multiple independent detector failures.

## Development vs holdout

Cases used while building RecallZero, including Mach-E 22V412000, belong in the development split. Holdout cases must remain untouched by detector changes until a Detector v2 proposal is frozen. Threshold, weights, taxonomy, severity, meta eligibility, or matching changes should be justified by multi-case development evidence and then evaluated once on the holdout set.

See `benchmarks/KNOWN_GAPS.md` for explicitly deferred detector issues.
