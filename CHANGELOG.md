# Changelog

## 0.3.3a2 — Parked-event severity guard

- Wire the existing parked-event cue scaffold into severity validation so a parked no-start/no-drive event cannot inherit unrelated background `while driving` text.
- Keep the veto narrow by requiring the extracted operating state to be `PARKED` and a parked-event cue to occur in incident context; moving failures are not suppressed merely because a later sentence mentions parking.
- Add production-shaped Mach-E regressions for the named moving/parked ODI cases, including the 11459506 background-motion bug shape.
- Add a split-sentence moving regression so valid narratives such as `I was driving... The car lost all power` remain accepted even if a later sentence says the vehicle was parked.
- Add non-scoring severity provenance (`severity_context`) showing whether motion context was accepted or suppressed by the parked-event guard.
- Lock the verified severity arithmetic: two `loss_of_motive_power` and two `vehicle_in_motion` complaints still score 83.5.
- Keep taxonomy, recall matching, Time Machine semantics, clustering, risk weights, threshold 75, and 28/84-day windows unchanged from 0.3.3a1.

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
