# RecallZero Detector v1 validation protocol

## Purpose

RecallZero 0.3.5/0.3.5.post1 evaluates **Detector Freeze v1** without changing detector scoring or representation behavior. 0.3.5.post1 revises only the NHTSA complaint input adapter before the cohort is locked. The benchmark layer may add provenance, locking, reporting, confidence intervals, and post-hoc adjudication, but it must not alter extraction, taxonomy, clustering, severity, trend, risk, meta-signal construction, recall-gap scoring, alert eligibility, or Time Machine leakage rules.

Mach-E / 22V412000 is a **development** case because it was repeatedly inspected while Detector v1 was being built. It is excluded from the clean Detector v1 validation sensitivity estimate.

## Splits

- `development`: cases already inspected during detector development.
- `validation`: preregistered cases scored once with frozen Detector v1. This split supports the Detector v1 generalization assessment.
- `holdout`: reserved for a later Detector v2. The CLI refuses to score it without `--confirm-holdout`.

Do not move or replace a validation case after scoring because its result is inconvenient. A miss remains a miss; an alerting control remains an alerting control and is adjudicated separately.

## Candidate selection

Positive cases are selected using the campaign's observable safety-defect class rather than known RecallZero score, known complaint spike, or known pre-recall complaint abundance. Complaint-addressable examples include loss of propulsion, braking, steering, thermal/fire, restraint, closure/structure, visibility, and electrical shutdown defects. Pure paperwork, label, certification, or owner-manual defects are not suitable for this detector.

Controls are **targetless replay windows**, not declarations that a vehicle has no safety defect. Prefer controls with meaningful complaint activity. A control alert can later turn out to be associated with a recall; this is evaluated only after raw detector output is persisted.

## Pre-registered prototype acceptance criteria

The default validation manifest records these criteria before scoring:

- positive sensitivity >= 0.60
- median qualified lead time > 0 days (configured minimum 1 day)
- unconfirmed alert lineages per vehicle replay-year <= 1.0
- control cases with >=1 unconfirmed alert <= 0.30
- all anti-leakage checks pass
- no invalid infrastructure cases
- configured minimum NIM signature fraction is met

These are prototype engineering criteria, not regulatory performance claims. Proportions are reported with Wilson 95% confidence intervals.

## Freeze and lock

1. `freeze-verify` checks Detector Freeze v1 source hashes and the live loaded risk/clustering/trend/model configuration.
2. `benchmark-preflight` checks case identity, campaign dates, vehicle resolution, complaint/recall availability, complaint-catalog query provenance, duplicate case IDs, and boundaries. It does **not** run extraction, clustering, risk scoring, or alerting. For the first 0.3.5.post1 validation preflight, use `--refresh`; stale complaint caches without the `nhtsa-complaint-catalog-v2` marker are rejected before locking.
3. `benchmark-lock` hashes the full candidate manifest, freeze manifest, and exact ordered case IDs for one split.
4. `benchmark --split validation` refuses to score without a matching lock.

The freeze includes detector modules such as `analytics/severity.py`, `analytics/risk_engine.py`, `pipeline.py`, and `recall/matcher.py`. The git commit is useful provenance, but the module/file SHA-256 values are the authoritative freeze for recovered archives without `.git` metadata.

The complaint input adapter is separately identified as `nhtsa-complaint-catalog-v2`. It resolves `/products/vehicle/models?issueType=c`, queries every conservative catalog family variant, and de-duplicates by ODI. This changes the completeness of benchmark inputs, not the Detector v1 scoring rules. The adapter revision and its source hash are pinned before the validation lock is created.

## Raw benchmark output

The raw benchmark is the audit artifact for what the detector knew. It persists:

- input complaint/recall fingerprints
- signature-cache fingerprint and semantic provenance
- case validity / invalid reason
- weekly snapshots
- full factor scores, weights, contributions, and explanations
- signed threshold margin (`risk_score - alert_threshold`)
- alert lineage persistence
- alert evidence needed for later adjudication
- post-hoc target score for positive cases only after each snapshot is frozen

Do not overwrite the raw artifact during adjudication.

## Control adjudication

Only after raw output exists may `benchmark-adjudicate` expose future recall information. Frozen alert lineages are classified as:

- `TARGET_RECALL_ASSOCIATED`
- `VISIBLE_RECALL_ASSOCIATED`
- `FUTURE_RECALL_ASSOCIATED`
- `UNCONFIRMED_ALERT`

`UNCONFIRMED_ALERT` is an operational false-alert burden label. It is **not** proof that the underlying safety pattern was invalid.

The adjudication horizon is preregistered with `adjudication_end_date`. Future recall text cannot change historical clusters, factor scores, risk scores, or alert decisions.

## Case validity

Infrastructure/evaluation failures can invalidate a case, including freeze mismatch, campaign/data retrieval failure, semantic degradation, NIM extraction failure under the configured provenance requirement, and anti-leakage failure. A legitimate detector miss, low complaint count, or low risk score is **not** an invalid case.

## Metrics

For positives:

- earliest target-like candidate date
- first alert date
- first qualified target-associated alert date
- lead time
- max risk and signed threshold margin
- factor composition at first alert
- evidence at first alert
- alert persistence

For controls:

- raw alert snapshots and lineages
- visible/future recall-associated alert lineages
- unconfirmed alert lineages and snapshots
- unconfirmed burden per vehicle replay-year
- control-case unconfirmed-alert rate

For aggregate proportions, report Wilson 95% confidence intervals.

## Known interpretation caveat

TAX-001 can fragment one physical pattern across multiple lineage IDs. A high unique-unconfirmed-lineage count must therefore be inspected for evidence overlap and lineage fragmentation before it is interpreted as many independent false alarms.

## Required live workflow

```bash
pytest -q

recallzero freeze-verify \
  --manifest benchmarks/detector_freeze_v1.yaml

recallzero benchmark-preflight \
  --manifest config/candidates.yml \
  --freeze benchmarks/detector_freeze_v1.yaml \
  --split validation \
  --refresh \
  --json data/runs/detector_v1_validation_preflight.json

recallzero benchmark-lock \
  --manifest config/candidates.yml \
  --freeze benchmarks/detector_freeze_v1.yaml \
  --split validation \
  --output benchmarks/detector_v1_validation.lock.json

recallzero benchmark \
  --manifest config/candidates.yml \
  --freeze benchmarks/detector_freeze_v1.yaml \
  --lock benchmarks/detector_v1_validation.lock.json \
  --split validation \
  --json data/runs/detector_v1_validation_raw.json \
  --csv data/runs/detector_v1_validation_raw.csv

recallzero benchmark-adjudicate \
  data/runs/detector_v1_validation_raw.json \
  --manifest config/candidates.yml \
  --json data/runs/detector_v1_validation_adjudicated.json

recallzero benchmark-report \
  data/runs/detector_v1_validation_adjudicated.json \
  --json data/runs/detector_v1_validation_summary.json \
  --csv data/runs/detector_v1_validation_summary.csv
```

Review the preflight before locking. After the validation benchmark has been scored, do not change Detector v1 or substitute cases while interpreting the result.
