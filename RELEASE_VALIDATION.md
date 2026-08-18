# RecallZero v2 Release Validation

Release version: **0.3.3a2**.

## Validated in the build environment

- `PYTHONPATH=src pytest -q -ra`: **63 tests passed, 1 optional NAT-runtime test skipped** because `nat` is not installed in the build container.
- `python -m compileall -q src tests`: passed.
- `python -m recallzero.cli --version`: reported `RecallZero 0.3.3a2`.
- `python -m recallzero.cli demo`: synthetic end-to-end analysis and Time Machine replay completed.
- Python wheel built without network build isolation and installed into an isolated target path; `RecallZero 0.3.3a2` imported successfully.
- Wheel SHA-256: `792bd3a33f2a6dd0e6df3701f14b73a3804dbe76578cd5e34d1e85efb80e2c0b`.

## 0.3.3a2 coverage

- Wires the existing `PARKED_EVENT_CUES` / `_parked_event` scaffold into motion validation.
- The parked veto requires both an extracted `PARKED` operating state and an incident-context parked cue, avoiding a document-wide ban on parking language.
- Production-shaped parked regressions cover `11459506`, `11463755`, `11464507`, and `11464559`; the 11459506 case contains the later/background `while driving` phrase that caused the observed false positive.
- Moving regressions cover `11415152`, `11460408`, `11462003`, `11465461`, `11465548`, and `11466150`.
- A split-sentence moving regression protects `I was driving... The car lost all power` style narratives even when later text says the vehicle was parked.
- An arithmetic regression locks the expected severity result at **83.5** after the parked false-positive `vehicle_in_motion` occurrence is removed.
- Evidence output now includes non-scoring `severity_context`, including `suppressed_by_parked_event` and `incident_motion_context`.

## Frozen by design

0.3.3a2 does **not** modify taxonomy logic, recall-matching weights/thresholds, meta-signal membership, clustering, alert threshold 75, risk weights, 28/84-day trend windows, minimum-evidence defaults, or anti-leakage rules.

## Target GB10 validation

Re-run the same three Mach-E experiments used for 0.3.3a1. The primary acceptance check is that ODI `11459506` no longer receives `vehicle_in_motion`, while the May 26 qualified alert remains independently determined by the unchanged risk engine. Do not tune the detector between the a1 and a2 comparisons.

## Non-claims

- The build environment does not execute live NHTSA/NVIDIA/NAT calls.
- Synthetic demo output is not a vehicle-safety finding.
- A historical lead-time claim still requires a qualified alert, post-hoc target match, and passing anti-leakage checks in the target replay.
