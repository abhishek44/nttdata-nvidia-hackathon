# Target Attribution Experiment A

## Purpose

Detector v1 validation showed that several positive vehicle populations produced pre-recall alerts that did not qualify against the historical target recall. The detailed audit also showed that these are heterogeneous cases: some alerts are plainly off-target, some target-like candidates never crossed the risk gate, and at least one case (Hyundai Sonata engine stall) is a plausible semantic-attribution miss.

Experiment A tests one category-level hypothesis without changing Detector v1:

> Replace only the current TF-IDF cosine term in post-hoc target attribution with cosine similarity from the configured NVIDIA embedding model.

The current structured target formula remains otherwise identical:

```text
0.30 mechanism
+ 0.20 consequence family
+ 0.20 component
+ 0.10 subsystem
+ 0.10 consequence text
+ 0.10 semantic similarity
```

The component-compatibility gate and 0.45 target threshold are also unchanged.

## Isolation guarantee

`TargetAttributor` is a separate evaluation-only class. It is **not** wired into `RecallMatcher.find_best_match`, which remains the live detector path used by `pipeline.py` to calculate recall-gap risk.

The experiment therefore performs:

- zero LLM extraction calls;
- zero detector replays;
- zero risk recalculations;
- zero clustering reruns;
- a bounded number of new embedding requests.

It reconstructs candidate clusters from the frozen `member_ids` stored in the raw benchmark plus local cached complaints/signatures. Before embedding, it recomputes the original TF-IDF target score and requires it to agree with the persisted raw benchmark score within 0.002. A mismatch stops the experiment because the cache/source provenance is no longer comparable.

## Scope

By default the command examines valid positive cases whose frozen detector status is `EARLY_ALERT_TARGET_UNMATCHED`.

For each case it includes:

1. every alert candidate, because unrelated alerts are the falsifiability guardrail; and
2. the ten candidates with the highest original post-hoc target score.

If a candidate belongs to both groups it is labeled `alert_and_top_target`.

## Run

Use the same data directory that contains the complaint/signature/recall caches used for the Detector v1 validation run. Do not refresh or re-extract the source cohort before this experiment.

```bash
export RECALLZERO_DATA_DIR=./data_detector_v1_localnim_post2

recallzero benchmark-attribution-experiment \
  data_detector_v1_localnim_post2/runs/detector_v1_validation_raw.json \
  --manifest config/candidates.yml \
  --target-match-threshold 0.45 \
  --top-target-count 10 \
  --json data_detector_v1_localnim_post2/runs/target_attribution_experiment_a.json
```

The configured embedding endpoint may remain NVIDIA-hosted while extraction is local. Experiment A uses only the embedding endpoint.

## How to judge it

Do **not** treat general score increases as success. The primary question is discrimination.

The known development audit provides falsifiable checks:

- Tesla Model Y unintended-braking alerts should remain below the defrost/heat-pump target threshold.
- Fusion's unrelated electrical alert should remain below the steering-wheel target threshold.
- Fusion's already-target-like steering candidate should remain target-like.
- Sonata's engine-stall candidate should rise materially if technical recall wording vs owner symptom wording is the missing semantic bridge.
- Grand Cherokee's engine/loss-of-motive-power candidate may rise, while its seat/airbag alert lineages should remain low.

A meaning-aware scorer that raises everything is not an improvement.

## Scientific status

This is a **development diagnostic**, not a new validation run. The 20-case cohort has already been inspected. If Experiment A earns a broader Target Attributor v2 design, select and lock an untouched holdout before tuning that v2 design.
