# Target Risk Audit

`recallzero benchmark-target-risk-audit` is a **read-only development diagnostic** for an already completed raw benchmark.
It answers one narrow question: among the positive case candidate lineages that were actually persisted in the raw benchmark, which target-plausible lineages failed because of target attribution, which failed because the frozen risk gate was not met, and which risk factors had the largest weighted headroom?

The command performs:

- zero LLM calls;
- zero embedding calls;
- zero detector replays;
- zero clustering/risk recomputation;
- no change to target scores, risk scores, or alert decisions.

It reads the raw benchmark's persisted candidate records only. RecallZero persists the ordinary top-N candidates by detector risk plus every alert. It does **not** persist every detector signal, so absence from this audit is not proof that no target-related signal existed.

## Run

```bash
recallzero benchmark-target-risk-audit \
  data_detector_v1_localnim_post2/runs/detector_v1_validation_raw.json \
  --target-match-threshold 0.45 \
  --top-lineages 8 \
  --json data_detector_v1_localnim_post2/runs/target_risk_audit.json
```

Upload only `target_risk_audit.json` for the next development decision.

## How to read it

For each positive case, the audit reports:

- the highest target-scoring persisted lineage;
- the maximum frozen risk score reached by that same lineage;
- whether that lineage ever alerted;
- the best target-scoring alerted lineage, if any;
- the frozen risk-factor decomposition at the lineage's maximum-risk snapshot;
- weighted headroom for severity, trend, persistence, evidence, and recall gap;
- a descriptive diagnosis such as `TARGET_MATCHED_BUT_RISK_GATE_NOT_MET`.

`weighted_headroom` is simply the extra contribution a factor could provide if its score were 100 while its frozen weight remained unchanged. It is a diagnostic ranking, **not** a recommendation to tune the factor.

## Scientific boundary

The Detector v1 validation result remains historical and unchanged. The current 20-case cohort is exposed development evidence. This audit is intended to determine the dominant Detector v2 failure category before any v2 change is made.
