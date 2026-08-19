# Detector v1 — Validation Baseline (freeze)

Summary
-------
- Benchmark: `detector-v1-validation-01` (freeze: `recallzero-detector-v1`).
- Cases: 20 preregistered (10 positives, 10 controls).
- Valid: 19; Invalid: 1 (infrastructure / extraction failure).
- Qualified positive sensitivity: 1 / 10 = 10% (95% CI ≈ 1.8%–40.4%).
- Positive-case any-alert rate: 6 / 10 = 60%.
- Valid control unconfirmed-alert rate: 1 / 9 = 11.1%.
- Acceptance: FAIL (sensitivity below preregistered threshold).

Preserved artifacts
-------------------
- Freeze manifest: `benchmarks/detector_freeze_v1.yaml`.
- Raw/summary/adjudicated outputs: `data_detector_v1_localnim_post2/runs/`.
- Matcher audits: `data_detector_v1_localnim_post2/runs/matcher_audit.json` and full audit artifacts.

Key findings (compact)
----------------------
- The 5 cases originally labeled "matcher failures" are heterogeneous:
  - Tesla Model Y: high-risk alert on unrelated phenomenon (driver assistance/unintended braking); no plausible recall-like alert was produced. This is not a matcher miss.
  - Ford Fusion: a correct target candidate was present (`target score ≈ 0.53`) but its risk score was too low (`risk ≈ 36`) to produce an alert — changing the matcher would not help.
  - Hyundai Sonata: shows the clearest target-attribution shortfall — the alert and structured axes indicate plausible alignment, but the current TF-IDF-based semantic_lexical component is weak (TF-IDF vs embeddings). This is the principal matcher-investigation case.
  - Jeep Grand Cherokee: near-threshold target candidate (`≈0.445`) but risk remains below alert gate; representation and competing off-target clusters matter.
  - VW ID.4: off-target / representation problem; alert cluster is unrelated.

Wrangler invalid case (infrastructure)
-------------------------------------
- Case: `Jeep Wrangler 2018` marked `INVALID_CASE` with `NIM_EXTRACTION_FAILURE`.
- Root cause: NIM returned an auxiliary severity indicator `airbag_deployed` not in the canonical allowed list. The strict structured-extraction contract treated this as a fatal schema violation and invalidated the case.
- Recommendation: treat unknown auxiliary LLM labels as non-fatal provenance warnings (preserve raw value), not case-fatal errors. Core schema failures (missing required fields or invalid JSON) should remain fatal.

Interpretation guidance
----------------------
- Report the validation as a scientific failure-but-informative baseline. Do NOT retroactively edit the original run to claim a pass after post-hoc fixes.
- Distinguish two rates in reports: (a) end-to-end qualified-target sensitivity (1/10 = 10%), and (b) positive-case any-alert rate (6/10 = 60%). Do not conflate the latter with target recall sensitivity.
- Use the full `matcher_audit.json` as the authoritative audit artifact; the compact summary may null-out some breakdown fields and should not be the source of final quantitative claims.

Recommended immediate actions (for a stable maintenance release)
---------------------------------------------------------------
1. Close Detector v1 as a frozen baseline and publish the validation artifacts unchanged.
2. Ship a small maintenance release (e.g., `0.3.5.post3`) containing only reliability and reporting fixes:
   - Non-fatal normalization of unknown NIM auxiliary severity labels (preserve provenance).
   - Left-censoring flag for lead_time_days when replay boundary equals the lead-time value (e.g., `>= 365`).
   - Add benchmark decomposition metrics (see checklist) to expose the off-target / on-target distinctions.
   - Add a built-in matcher-audit export command to generate the authoritative `matcher_audit.json`.

Next scientific phase
---------------------
- Use the current 20-case cohort as the development set for `TargetAttributorV2` and Detector v2 work.
- Pre-register and freeze an untouched holdout cohort (recommended: 10 positive + 10 control) before any v2 algorithmic development.
- TargetAttributorV2 should combine real embedding similarity with structured axes (component, mechanism, consequence) and soft component-compatibility handling; its outputs must be evaluation-only and must not feed live risk scoring.

Appendix: important files
-------------------------
- Freeze manifest: `benchmarks/detector_freeze_v1.yaml`
- Validation outputs (raw/summary/adjudicated): `data_detector_v1_localnim_post2/runs/`
- Wrangler failure: `data_detector_v1_localnim_post2/runs/wrangler_failure.json`
- Matcher audits: `data_detector_v1_localnim_post2/runs/matcher_audit.json` (full audit), `matcher_audit_summary.json` (compact)

---
Document prepared by: automated repo audit
Date: 2026-08-19
