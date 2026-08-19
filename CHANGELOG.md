# Changelog

## 0.3.5.post5 — Long-horizon recurrence separation experiment

- Keep Detector v1 scoring, the 75-point alert threshold, all risk weights, severity, taxonomy, clustering, trend windows, visible-recall matching, and Time Machine behavior unchanged.
- Add `recallzero benchmark-recurrence-separation`, a read-only development experiment over a freeze-verified and lock-verified raw benchmark artifact.
- Compare the best persisted post-hoc target lineage in each valid positive case against the highest frozen max-risk lineage in each valid negative control. Positive target selection remains evaluation-only and cannot feed live detector scoring.
- Define recurrence conservatively from increases in the running maximum independent `evidence_count`; repeated weekly persistence without new evidence is not counted as recurrence.
- Report three fixed screening gates plus continuous medians and pairwise AUC values for evidence-growth months, evidence-growth span, growth events, evidence gain after first persistence, and maximum evidence.
- Perform zero LLM calls, zero embedding calls, zero detector replays, zero clustering reruns, and zero risk recomputation.
- Preserve the historical Detector v1 Validation Run 1 result and explicitly treat the exposed 20-case cohort as development evidence only.
- Archive the exact 0.3.5.post4 freeze manifest before recording the post5 diagnostic hashes.


## 0.3.5.post4 — Read-only target-risk audit diagnostic

- Keep Detector v1 extraction, taxonomy, clustering, severity, trend, risk, live recall-gap matching, post-hoc target matching, alert threshold, weights, and Time Machine behavior unchanged from 0.3.5.post3.
- Add `recallzero benchmark-target-risk-audit` to inspect deterministic risk-factor breakdowns already persisted in a freeze/lock-verified raw benchmark.
- The audit performs zero LLM calls, zero embedding calls, zero detector replays, and zero risk recomputation. It reads only persisted top-N-by-risk candidates plus every alert.
- Group candidate occurrences by lineage, separate best target score from maximum frozen risk on the same lineage, and report the largest weighted factor headroom without treating that headroom as a tuning recommendation.
- Add explicit diagnoses such as `TARGET_MATCHED_BUT_RISK_GATE_NOT_MET` and `BEST_TARGET_LINEAGE_NOT_ALERTED_AND_OFF_TARGET_ALERTS_PRESENT`.
- Preserve archived 0.3.5.post2 and 0.3.5.post3 freeze manifests under `benchmarks/archive/` for historical provenance.
- Add three regression tests covering target-vs-off-target lineage separation, weighted risk-headroom arithmetic, and rejection of unverified source benchmarks.

## 0.3.5.post3 — Stability normalization + isolated embedding attribution experiment

- Keep Detector v1 calibration, live visible-recall matching, risk weights, 75-point alert threshold, taxonomy, clustering, severity scoring, trend windows, and Time Machine leakage rules unchanged.
- Treat unsupported auxiliary NIM `severity_indicators` labels (for example the observed Wrangler `airbag_deployed`) as non-fatal vocabulary drift: log and drop them from the canonical signature before detector use. Core structured extraction failures such as invalid JSON or missing required fields still receive one repair attempt and remain fail-closed in strict benchmarks.
- Add an evaluation-only `TargetAttributor` that is structurally separate from `RecallMatcher.find_best_match`; live `recall_gap` risk cannot call the experimental embedding path.
- Add `recallzero benchmark-attribution-experiment` to compare the frozen TF-IDF target-attribution semantic term against NVIDIA embedding cosine while keeping the exact 10% semantic weight, structured axes, component gate, 0.45 threshold, frozen risk scores, and alert decisions unchanged.
- The attribution experiment uses persisted candidate `member_ids` plus local complaint/signature/recall caches, recomputes the baseline target score as a provenance check, performs zero LLM calls and zero detector replays, batches unique embedding texts, and stops if the reconstructed baseline drifts from the frozen raw artifact.
- Mark Experiment A explicitly as development-only and require discrimination rather than general score inflation: unrelated alert candidates are retained as falsifiability guardrails.
- Add regression tests for Wrangler auxiliary-label normalization, exact 10% embedding substitution arithmetic, preservation of the component gate, and cache-only attribution execution.

## 0.3.5.post2 — Local NIM structured-extraction reliability gate

- Keep Detector v1 calibration and downstream scoring behavior frozen: no changes to severity, taxonomy, clustering, meta-signal construction, trend/risk math, recall matching, alert threshold, weights, windows, or Time Machine leakage rules.
- Strengthen the NIM guided JSON schema with explicit non-empty constraints for `system` and `failure_mode`; the extraction prompt now forbids null/empty failure modes and uses `UNSPECIFIED FAILURE` when the complaint cannot support a more specific label.
- Add exactly one deterministic NIM schema-repair attempt when an HTTP-success response is invalid JSON or fails Pydantic validation. A successful repair remains `extraction_method=nim`; a second invalid response raises `NIMStructuredExtractionError`.
- Make validation benchmarks with `minimum_nim_fraction: 1.0` fail closed on any NIM extraction or transient NIM failure instead of continuing through heuristic fallback. Interactive/non-benchmark analysis retains the existing heuristic fallback behavior.
- Add regressions for the five observed local-NIM null-`failure_mode` ODIs (11443132, 11443932, 11448380, 11459714, 11466251), failed repair behavior, schema `minLength`, and strict-vs-interactive fallback semantics.
- Refresh Detector Freeze v1 source hashes before validation locking and record `semantic_extraction_revision: local-nim-structured-contract-v2`.

## 0.3.5.post1 — Complaint catalog adapter correction + NAT compatibility test fix

- Detector v1 scoring/representation logic remains frozen; no changes to severity, taxonomy, clustering, meta-signal construction, trend, risk, recall matching, thresholds, weights, or Time Machine leakage rules.
- Complaint ingestion now resolves NHTSA's complaint product catalog (`issueType=c`) and aggregates conservative model-family variants before ODI de-duplication. This addresses marketed-family lookups such as Tesla Model Y and Ford F-150 where ODI records may be partitioned across catalog variants or a generic endpoint query may be rejected.
- HTTP 400 is tolerated only as a rejected complaint-model variant while other catalog-resolved variants are tried; non-400 HTTP failures remain fatal to avoid silently accepting partial benchmark input.
- Raw complaint payloads now preserve adapter/query provenance, including requested model, catalog-resolved models, queried/successful variants, per-variant counts, rejected HTTP-400 variants, and de-duplicated ODI count.
- Benchmark preflight surfaces complaint adapter/model-variant provenance and rejects stale complaint caches that lack the `nhtsa-complaint-catalog-v2` marker, directing operators to rerun with `--refresh` before locking the cohort.
- Fixed the NAT registration compatibility regression test: its dynamic `exec()` unintentionally inherited this test module's `from __future__ import annotations`, producing string annotations unlike the production `recallzero.aiq.register` module. The fixture now compiles with `dont_inherit=True`, accurately exercising eager runtime annotations on NAT 1.8.


## 0.3.5 — Detector v1 validation harness

- Keep Detector Freeze v1 byte-for-byte unchanged while extending only evaluation/schema/CLI code.
- Add preregistered `development`, `validation`, and guarded `holdout` benchmark splits.
- Add `benchmark-preflight` for NHTSA identity/date/data checks without detector scoring.
- Add manifest locking and validation/holdout lock enforcement; holdout also requires explicit confirmation.
- Preregister a 10-positive / 10-targetless-control Detector v1 validation cohort in `config/candidates.yml`; Mach-E remains development-only.
- Persist signed threshold margins, full risk-factor breakdowns, alert persistence, input-data fingerprints, semantic/NIM provenance, and explicit case validity.
- Add post-hoc control adjudication with `VISIBLE_RECALL_ASSOCIATED`, `FUTURE_RECALL_ASSOCIATED`, and `UNCONFIRMED_ALERT`; future recall data is exposed only after raw detector output is frozen and persisted.
- Add Wilson 95% confidence intervals and richer validation aggregate/report metrics.
- Make benchmark comparison lineage-aware by matching `(cutoff_date, lineage_id)` and add best-effort split/merge diagnostics.
- Add `docs/BENCHMARK_PROTOCOL.md` and keep TAX-001 / META-001 explicitly deferred.

## 0.3.4 — Detector Freeze v1 benchmark infrastructure

- Freeze detector behavior at the 0.3.3a2 state; no intentional changes to severity math, taxonomy, clustering, meta-signal construction, trend/risk scoring, recall matching, thresholds, weights, or leakage rules.
- Add `benchmarks/detector_freeze_v1.yaml` with explicit hashes for detector-critical modules including severity, risk, trend, taxonomy, clustering, `pipeline.py`, and `recall/matcher.py`, plus evaluation-layer hashes.
- Verify the freeze against live loaded `Settings().risk_config()`, clustering/trend settings, model identifiers, and the actual active `risk.yml` hash rather than trusting duplicated manifest values.
- Persist full severity/trend/persistence/evidence/recall-gap score, weight, contribution, and explanation fields on every `BacktestCandidate` top-candidate snapshot.
- Extend `config/candidates.yml` with benchmark role/split metadata while preserving the existing vehicle/campaign schema.
- Add `recallzero benchmark --manifest ...`, positive historical replays, targetless negative-control replays, JSON/CSV outputs, aggregate sensitivity/lead-time/false-alert metrics, and lineage-fragmentation caveats.
- Add `recallzero freeze-verify` and `recallzero benchmark-compare` commands.
- Track TAX-001 (OTHER consequence consistency bypass) and META-001 (over-broad mechanism meta-signal membership) as benchmark-driven backlog items instead of changing detector logic.

## 0.3.3a1 — Scientific slice A

- Keep detector calibration frozen: alert threshold 75, risk weights, 28/84-day windows, DBSCAN eps/min_samples, and anti-leakage rules are unchanged.
- Repair event-scoped severity false negatives for moving shutdown/stall/power-loss narratives while preserving parked no-start/no-drive negatives.
- Add regression cases based on the Mach-E ODI examples 11415152, 11460408, 11462003, 11465461, 11465548, 11466150 (positive) and 11459506, 11463755, 11464507, 11464559 (negative).
- Add a narrow taxonomy consistency guard that downgrades contradictory specific mechanism/consequence combinations to component-level GENERAL/OTHER categories instead of feeding them into meta aggregation.
- Make recall matching consider the observed consequence-family distribution across a signal, while preserving the existing mechanism/component/subsystem/text score structure.
- Add evaluation-only `earliest_target_like_candidate_date`/score/signal fields and `first_qualified_alert_date`/signal fields so target-pattern recognition is clearly separated from a defensible lead-time claim.
- Deliberately defer the investigation-signal fusion graph and meta-membership confidence rules to a later isolated experiment.

## 0.3.2

- Added dual-axis defect representation: root `failure_mechanism` plus driver-visible `consequence_family`.
- Added cross-component `meta` signals that reconnect fragmented child clusters while preserving ODI and child-cluster lineage.
- Added stable `lineage_id` values across weekly replay snapshots.
- Changed complete-link refinement guard to enforce maximum sampled pairwise dispersion rather than only p90 dispersion.
- Added event-scoped severity validation to reject background, negated, and hypothetical motion/safety references.
- Reworked persistence into active weeks over four weeks plus maximum consecutive active weeks over eight weeks.
- Added component-compatibility gating and dual-axis scoring to recall matching.
- Added Time Machine top-candidate diagnostics, post-hoc target scores, max risk, and distance-to-alert without exposing target recall text during detection.
- Kept the 75/100 alert threshold, risk weights, minimum evidence defaults, and leakage boundary unchanged.


## 0.3.1

- Add deterministic canonical defect families for cross-wording normalization while preserving raw NIM failure modes as evidence.
- Refine clustering to component family -> canonical defect family -> DBSCAN, then split DBSCAN density chains with complete-link cosine refinement.
- Add canonical defect-family purity and per-cluster cosine-distance diagnostics alongside raw failure-mode purity.
- Replace LLM-trusted severity flags with deterministic validation: crash/injury/fatality/fire source truth comes from NHTSA structured fields; other safety indicators require explicit narrative support.
- Expose validated severity indicators and evidence provenance on each evidence item; unsupported LLM safety indicators are rejected from risk scoring.
- Replace lexical-only recall matching with structured scoring across defect family, component, subsystem, consequence, and semantic text, with exact campaign-reference sanity checks.
- Raise the Time Machine post-hoc target-match threshold to 0.45 for the new structured score.
- Distinguish `EARLY_ALERT_TARGET_UNMATCHED` from true `NO_EARLY_SIGNAL`, and expose the first risk-qualified alert separately from the first target-matching alert.
- Preserve all anti-leakage rules; target recall text is still introduced only after each snapshot signal is frozen.
- Add scientific-validity regression tests for hypothetical crash rejection, parked-motion rejection, taxonomy separation, explicit campaign matching, HV power-loss recall matching, and backtest outcome semantics.

## 0.3.0

- Add hosted-NIM rate-limit resilience with Retry-After support, exponential backoff, jitter, and transient HTTP classification.
- Reduce default LLM extraction concurrency from 4 to 2 and raise the retry budget for long complaint runs.
- Refuse silent heuristic/TF-IDF fallback after exhausted 429/5xx transient failures by default; this can be explicitly overridden with `RECALLZERO_TRANSIENT_NIM_FALLBACK=true`.
- Persist successful failure signatures incrementally and checkpoint small batches so interrupted/rate-limited runs resume without repeating completed ODI records.
- Replace flat semantic clustering with hierarchical NHTSA component-family -> DBSCAN clustering.
- Add cluster diagnostics: component-group sizes, DBSCAN cluster/noise counts, cosine-distance summaries, largest-cluster share, and failure-mode purity.
- Mark suspicious one-cluster or dominant-cluster results with explicit quality warnings and expose `semantic_quality` on analysis runs.
- Prevent degraded semantic snapshots from producing a positive Recall Time Machine lead-time claim.
- Compact NAT tool output by truncating evidence-ID arrays and adding representative evidence IDs, exact metric-window definitions, quality diagnostics, and grounding rules.
- Switch the modern NAT workflow to `tool_calling_agent`, remove the unsupported `thinking` YAML field, and use only NAT 1.8 documented workflow fields.
- Strengthen deterministic evidence-critic checks with NHTSA component-family alignment and generic-cluster warnings.
- Add regression tests for 429 retry behavior, permanent-4xx behavior, transient-fallback refusal, hierarchical component separation, and NAT 1.8 configuration.

## 0.2.2

- Fix structured complaint extraction for reasoning-capable Nemotron 3.5 models by disabling thinking for extraction requests.
- Use NVIDIA NIM guided JSON generation with a strict Pydantic failure-signature schema.
- Improve empty-response diagnostics with finish reason and reasoning-content length.
- Log a truncated raw model-output prefix when structured extraction validation fails.
- Fix NeMo Agent Toolkit custom-function construction by using eager annotations and explicit ``input_schema`` declarations.
- Set ``thinking: false`` for the standalone NAT ReAct investigator configuration.
- Add a degraded semantic-quality warning when more than 20% of complaint signatures fall back to heuristics.
- Add regression tests for guided JSON request payloads, structured extraction validation, and NAT type-hint resolution.

## 0.2.1

- Resolve NHTSA complaint/recall model punctuation mismatches through the official product-model catalog.
- Add safe punctuation-only recall-model fallbacks when the catalog is unavailable.
- Stop retrying non-transient HTTP 400 responses; retain retries for transport errors, rate limiting, and server failures.
- Parse NHTSA complaint slash dates as month-first and recall slash dates as day-first, matching the two endpoint formats.
- Record the requested and resolved recall model in cached raw recall responses.
- Mark the deprecated `aiq` module as optional in `recallzero doctor`; current integrations use `nat`.
- Support both the NAT 1.8+ public plugin API and the documented NAT 1.5-1.7 registration imports.
- Clarify that the synthetic demo intentionally uses heuristic extraction.
- Add a working `recallzero --version` option for upgrade verification.
- Add regression tests for Mustang Mach-E/Mustang Mach E resolution and retry behavior.

## 0.2.0

- Initial clean RecallZero v2 rebuild with NHTSA ingestion, NIM and deterministic fallbacks, clustering, trend/risk analytics, Recall Time Machine, API/dashboard, and NAT/AI-Q integrations.
