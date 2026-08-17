# Recall Time Machine Backtest Protocol

## Purpose

The backtest asks whether RecallZero could have produced a credible warning using only information available before a known historical recall. It must not use the future campaign description to guide detection.

## Required inputs

- Exact vehicle population: make, model, model year(s).
- Target NHTSA campaign number.
- Official recall boundary date.
- Complaint records with reliable filing dates.
- Recall records with report-received dates.
- Frozen risk, clustering, and matching configuration.

## Leakage controls implemented in code

1. `complaint.received_date < official_recall_date` is mandatory.
2. Every replay cutoff is strictly before the official recall date.
3. The normal detector sees only recalls whose report date is at or before each replay cutoff.
4. The target recall description is not passed into extraction, embedding, clustering, trend, severity, or risk.
5. Each weekly alert set is frozen before target recall text is used for post-hoc correspondence scoring.
6. If the target campaign becomes visible during detection, the backtest is invalidated.
7. Anti-leakage checks are persisted in the output.

## Replay procedure

```text
Choose case and freeze configuration
              |
              v
Load complaints strictly before recall
              |
              v
Extract signatures once from pre-recall evidence
              |
              v
Replay weekly cutoffs
  - visible complaints only
  - visible recalls only
  - clusters, trend, risk, alert gate
              |
              v
Freeze alerts at each cutoff
              |
              v
Post-hoc target recall matching
              |
              v
First matching alert -> lead time
Risk alert exists but target unmatched -> EARLY_ALERT_TARGET_UNMATCHED
No risk-qualified alert                -> NO_EARLY_SIGNAL
```

By default the replay begins no more than one year before the recall. A different start date can be supplied, but it must be declared before inspecting results.

## Outcome definitions

- `EARLY_SIGNAL_DETECTED`: at least one frozen alert passes the target post-hoc match threshold before the official date and all anti-leakage checks pass.
- `EARLY_ALERT_TARGET_UNMATCHED`: at least one frozen pre-recall alert passes the risk gate, but none passes the post-hoc target-recall match threshold.
- `NO_EARLY_SIGNAL`: no frozen pre-recall alert passes the risk gate.
- `INVALID_BACKTEST`: one or more anti-leakage checks fail.

Lead time is:

```text
official recall date - first matching alert cutoff
```

## What does not count as validation

- Selecting a case after looking at complaint trajectories and reporting it as unbiased.
- Rewriting extraction prompts using the target recall language.
- Searching for target words before clusters are frozen.
- Lowering thresholds until the target case succeeds.
- Using post-recall complaints or complaint updates.
- Measuring only positive cases.
- Claiming that a matched cluster proves the recalled defect existed in every report.

## Recommended evaluation set

Use at least:

- 3-5 historical positive campaigns across different systems.
- Negative-control vehicle/time windows with no corresponding recall.
- Near-miss controls: high complaint volume but stable trend.
- Recall-covered controls: growing clusters already covered at the cutoff.

Report:

- Case-level detection status.
- Lead time distribution.
- Precision and false-alert rate.
- Alert burden per vehicle-month.
- Sensitivity to risk threshold and minimum evidence.
- Cluster stability under parameter/model changes.
- Extraction fallback rate.
- Recall-match review accuracy.

## Reproducibility record

Persist for every reported experiment:

- Git commit/archive checksum.
- NHTSA fetch timestamp and raw response cache.
- Model names/endpoints.
- Extraction method counts.
- Embedding method.
- failure-mechanism/consequence-family rules, meta-signal aggregation rules, DBSCAN parameters, and complete-link refinement threshold.
- Risk configuration.
- Official recall date source.
- Replay start date.
- All anti-leakage checks.

## Current candidate

`config/candidates.yml` includes Ford Mustang Mach-E campaign `22V412000` with a `2022-06-10` boundary because it appeared in the recovered project notes. It remains an unvalidated candidate until real pre-recall data are run and reviewed. A negative outcome is acceptable and must be preserved.

## 0.3.2 evaluation-only candidate diagnostics

After each detector snapshot is frozen, the Time Machine records up to five highest-risk candidates with `max_risk_score`, `distance_to_alert_threshold`, stable lineage, and an evaluation-only `posthoc_target_score`. Target recall text is introduced only after the detector output is frozen and never changes clustering, trend, severity, recall-gap scoring, or the alert decision. This allows a negative backtest to distinguish "the detector looked at the right mechanism but stayed below threshold" from "the detector never surfaced a target-like pattern."
## 0.3.3a diagnostic separation

The Time Machine now emits two explicit evaluation fields in addition to the existing alert fields:

- `earliest_target_like_candidate_date`: earliest frozen top candidate whose post-hoc target score crosses the configured target-match threshold. This is **not** an alert and must not be reported as lead time.
- `first_qualified_alert_date`: earliest frozen detector alert that also passes the post-hoc target-recall match threshold. This is the alert date eligible for a lead-time claim when all anti-leakage checks pass.

The target recall is still introduced only after each detector snapshot is frozen. 0.3.3a1 keeps the detector threshold, risk weights, trend windows, minimum evidence defaults, DBSCAN parameters, and anti-leakage rules unchanged.

