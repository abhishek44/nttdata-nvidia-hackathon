# RecallZero v2 Release Validation

Release version: **0.3.5.post3** — stability normalization and isolated embedding target-attribution experiment.

## Scope

Detector v1 Validation Run 1 on 0.3.5.post2 is preserved as the historical validation result. It did not pass the preregistered acceptance bar. post3 does not reinterpret that outcome and does not tune the live detector against the exposed validation cohort.

The maintenance release addresses one concrete runtime robustness failure and adds one isolated development experiment:

1. The Jeep Wrangler control failed because local Nemotron emitted an otherwise valid signature containing an unsupported auxiliary severity label, `airbag_deployed`, even after the repair attempt. post3 makes unsupported auxiliary labels non-fatal while keeping core structured extraction strict.
2. The detailed matcher audit showed that the current post-hoc semantic term is TF-IDF cosine at exactly 10% weight. post3 adds an evaluation-only embedding substitution experiment without changing live recall-gap matching or detector risk.

## Wrangler stability correction

- `severity_indicators` remains prompt-constrained to the canonical RecallZero vocabulary, but the guided schema accepts strings rather than enforcing a hard enum at Pydantic validation time.
- Unsupported labels are logged with the ODI and dropped before the `FailureSignature` enters canonical detector semantics.
- No unsupported label is mapped to a recognized safety indicator. In particular, `airbag_deployed` is **not** promoted to `restraint_failure`.
- Deterministic `SeverityEngine` validation remains authoritative.
- Invalid JSON, missing/empty `system` or `failure_mode`, malformed field types, or other core schema failures still receive one NIM-only repair attempt and remain fail-closed in strict validation.

## Target Attribution Experiment A

`TargetAttributor` is a new evaluation-only class and is not wired into `RecallMatcher.find_best_match`, the live detector path used by `pipeline.py` to calculate recall-gap risk.

Experiment A changes exactly one term:

```text
baseline:     0.10 * TF-IDF cosine
experiment:   0.10 * NIM embedding cosine
```

Everything else remains the frozen post-hoc formula: mechanism 30%, consequence family 20%, component 20%, subsystem 10%, consequence text 10%, the component gate, and target threshold 0.45.

The `benchmark-attribution-experiment` command:

- reads a freeze/lock-verified raw benchmark artifact;
- selects valid positive `EARLY_ALERT_TARGET_UNMATCHED` cases;
- includes every alert candidate plus the highest baseline target candidates;
- reconstructs candidate clusters from persisted `member_ids` and local complaint/signature/recall caches;
- recomputes the original TF-IDF target score and refuses to continue if it differs from the frozen raw score by more than 0.002;
- performs zero LLM calls and zero detector replays;
- batches unique texts through the configured NIM embedding model;
- writes before/after candidate-level scores for discrimination analysis.

The experiment is development-only. A score increase is not automatically a success; off-target alert candidates must remain below the target threshold.

## Scientific boundary

- The 20-case Detector v1 validation cohort is now exposed and can only be used as a development/audit set.
- If Experiment A supports Target Attributor v2, a new untouched holdout must be selected and locked before v2 tuning.
- The 75-point detector threshold and live risk formula remain unchanged in post3.

## Build validation

- `PYTHONPATH=src pytest -q -ra`: **105 collected, 104 passed, 1 skipped, 0 failed**.
- The one skip is the optional NAT runtime integration because `nat` is not installed in the isolated build environment.
- `python -m compileall -q src tests`: **PASS**.
- `PYTHONPATH=src python -m recallzero.cli --version`: **RecallZero 0.3.5.post3**.
- `freeze-verify --manifest benchmarks/detector_freeze_v1.yaml`: **PASS** against live Settings, `risk.yml`, detector hashes, and evaluation hashes.
- `benchmark-attribution-experiment --help`: **PASS**; command is present and explicitly described as no-detector-replay evaluation.
- Wheel build via `pip wheel --no-deps --no-build-isolation`: **PASS**.
- Isolated-target wheel import reports **0.3.5.post3** and exposes `TargetAttributor`.
- Detector-module comparison against 0.3.5.post2: **14 unchanged / 1 changed**. The sole detector-section change is `intelligence/extractor.py`; `pipeline.py`, `recall/matcher.py`, severity, risk engine, and `config/risk.yml` are byte-identical.
- Ruff is not installed in the isolated build environment, so no Ruff result is claimed.
- Wheel SHA-256: `38c7d5571befd3f0dc4fbbcb2f6587ccd04265cab1a1aa837a099594fa53bb7c`.
