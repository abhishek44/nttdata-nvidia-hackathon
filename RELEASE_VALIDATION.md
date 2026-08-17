# RecallZero v2 Release Validation

Release version: **0.3.3a1** (the requested 0.3.3a scientific slice; `a1` is the PEP 440 package version).

## Validated in the build environment

- `PYTHONPATH=src pytest -q -ra`: **60 tests passed, 1 optional NAT-runtime test skipped** because `nat` is not installed in the build container.
- `python -m compileall -q src tests`: passed.
- `python -m recallzero.cli --version`: reported `RecallZero 0.3.3a1`.
- `python -m recallzero.cli demo`: synthetic end-to-end analysis and Time Machine replay completed.
- Python wheel built successfully without network build isolation and installed into an isolated target path; `RecallZero 0.3.3a1` loaded from the wheel.
- Wheel SHA-256: `69712149bea2bad7566fbdab6845e71ae512cd99eeb9ee0dbbe26339d3d71c9b`.

## New 0.3.3a slice coverage

- Moving-event positive regressions for ODI examples `11415152`, `11460408`, `11462003`, `11465461`, `11465548`, and `11466150`.
- Parked/no-start negative regressions for ODI examples `11459506`, `11463755`, `11464507`, and `11464559`.
- Loss-of-motive-power now requires event-scoped motion support, while shutdown/stall/dead-throttle and contextual unable-to-move phrases have broader deterministic coverage.
- Contradictory specific mechanism/consequence assignments are downgraded to component-level `GENERAL_*`/`OTHER` categories.
- Meta recall matching uses the member consequence-family distribution while retaining the same mechanism/component/subsystem/text structure.
- Time Machine exports `earliest_target_like_candidate_date` separately from `first_qualified_alert_date`.

## Frozen by design

The following were **not changed** in this slice: alert threshold, risk weights, recent/baseline windows, minimum evidence defaults, DBSCAN `eps`, DBSCAN `min_samples`, complete-link threshold, meta-signal aggregation architecture, and anti-leakage rules. The proposed investigation-signal fusion graph and meta-membership confidence heuristic are deferred.

## Requires validation on the target GB10 environment

1. Preserve `.env` and `data/cache`; install 0.3.3a1.
2. Re-run the same current-state Mach-E analysis used for 0.3.2.
3. Re-run the same `--cutoff 2022-06-09` analysis.
4. Re-run campaign `22V412000` with recall date `2022-06-10`.
5. Compare the 0.3.2 and 0.3.3a1 outputs, especially severity factor counts, meta-signal risk, `earliest_target_like_candidate_date`, `first_qualified_alert_date`, `first_matching_alert_date`, and anti-leakage checks.
6. Do not tune the 75-point threshold based on this single candidate.

## Non-claims

- The build environment did not execute live NHTSA/NVIDIA/NAT calls.
- A target-like candidate is not an alert and is not a lead-time result.
- Any Mach-E outcome change must be attributed to this narrow slice before deciding whether the deferred fusion/meta-confidence work is needed.
