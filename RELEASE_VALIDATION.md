# RecallZero v2 Release Validation

Release version: **0.2.2**

## Validated in the build environment

- `pytest -q`: **30 tests passed, 1 optional NAT-runtime test skipped** because `nvidia-nat` is not installed in the build container.
- `python -m compileall -q src tests`: passed.
- `recallzero --help`: command entry point loaded.
- `recallzero demo`: synthetic end-to-end analysis and Time Machine replay completed.
- Python wheel built, installed into an isolated package target using the build environment's dependencies, inspected for package files/dashboard assets/CLI and plugin entry points, and used to run the synthetic demo.
- Stock AI-Q configuration patcher tested against representative shallow/deep researcher and ReAct YAML structures, plus the current AI-Q `data_source_registry` inheritance pattern.

## Covered by automated tests

- Complaint and recall normalization.
- NHTSA complaint response parsing, campaign-number lookup, recall-catalog model resolution, endpoint-specific date parsing, and non-transient retry behavior.
- Heuristic failure extraction and multi-component selection.
- NIM/local-endpoint configuration detection.
- NIM guided-JSON request payloads, thinking-disabled structured extraction, fenced-JSON recovery, and empty-content diagnostics.
- NAT tool schema registration uses eager runtime annotations and explicit `input_schema` values.
- Cache upgrade from heuristic signatures to a configured NIM model.
- TF-IDF/DBSCAN semantic grouping.
- Deterministic trend, severity, and risk behavior.
- Prevention of source-flag double counting.
- Recall Time Machine exclusion of post-recall records.
- Target-recall leakage invalidation.
- Evidence-level cutoff checks.
- FastAPI health, dashboard, and offline demo routes.
- AI-Q/NAT configuration patching.

## Requires validation on the target GB10 environment

The build environment did not contain the user's NVIDIA credentials or installed stock AI-Q runtime. Therefore these checks must be run on GB10:

1. Live NHTSA retrieval.
2. Hosted NVIDIA NIM chat and embedding calls, or local NIM endpoints.
3. NeMo Agent Toolkit plugin discovery with `nat info`/`nat run`.
4. The user's exact stock AI-Q Blueprint version and custom CLI using the patched configuration.
5. The real Mustang Mach-E historical experiment and additional positive/negative controls.

## Non-claims

- The synthetic demo is an installation smoke test, not a real safety result.
- The configured Mustang Mach-E campaign is an experiment candidate, not a validated early-warning result.
- Prototype thresholds and recall-matching similarity must be calibrated before production use.
