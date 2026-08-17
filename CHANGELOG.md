# Changelog

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
