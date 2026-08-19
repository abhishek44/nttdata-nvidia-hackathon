# Troubleshooting

## `Legacy aiq compatibility: not installed`

This is informational, not a failure. Current NVIDIA AI-Q Blueprint releases use NeMo Agent Toolkit through the `nat` Python module and `nat` CLI. RecallZero supports both the NAT 1.8+ public plugin facade and the documented registration surface used by NAT 1.5-1.7. The old `aiq` module is a deprecated compatibility layer and is not required for RecallZero's current integration.

The relevant checks are:

```text
NeMo Agent Toolkit module  installed  OK
NeMo Agent Toolkit CLI     .../nat    OK
Legacy aiq compatibility   not installed (not required)  OPTIONAL
```

Install the legacy extra only when an older environment explicitly launches workflows with the `aiq` CLI:

```bash
python -m pip install -e ".[aiq-legacy]"
```

Do not install it merely to make the optional doctor row green.

## The offline demo reports heuristic signatures

`recallzero demo` deliberately disables NVIDIA NIM so it can run without network access or credentials. The warning that signatures used the deterministic heuristic path is expected. Use a real `analyze` command without `--no-nim` to exercise the configured NVIDIA model.

## NHTSA recalls return HTTP 400 for a model accepted by complaints

NHTSA's complaint and recall catalogs sometimes use different punctuation for the same model. A known example is:

```text
Complaint catalog/request: MUSTANG MACH-E
Recall catalog:           MUSTANG MACH E
```

RecallZero 0.2.1 resolves the exact recall-catalog model through:

```text
/products/vehicle/models?modelYear=<year>&make=<make>&issueType=r
```

It then calls `recallsByVehicle` with the returned spelling. Safe punctuation-only variants are attempted if the product catalog is temporarily unavailable. Ordinary HTTP 400 responses are no longer retried repeatedly.

After upgrading, rerun the original command. The successful log should contain a line similar to:

```text
Resolved NHTSA recall model 'MUSTANG MACH-E' to catalog value 'MUSTANG MACH E' for 2021 FORD
```

Because the first failed run already cached complaints, `--refresh` is not required:

```bash
recallzero fetch \
  --make FORD \
  --model "MUSTANG MACH-E" \
  --years 2021,2022
```

Use `--refresh` only when you want to refetch complaints as well as recalls.

## Confirm the active executable after an upgrade

```bash
which recallzero
recallzero --version
python -c "import recallzero; print(recallzero.__version__, recallzero.__file__)"
```

All three should refer to the intended virtual environment and version.


## NIM returns HTTP 200 but every extraction falls back to heuristics

RecallZero 0.2.2 sends a guided JSON schema and disables model thinking for the extraction call. This is important for reasoning-capable models such as ``nvidia/nemotron-3.5-lightning-30b-a3b`` because reasoning and visible output share the completion budget. If extraction still falls back, rerun with ``RECALLZERO_LOG_LEVEL=DEBUG`` and inspect the structured-extraction warning.

A healthy bounded validation should report NIM signatures in ``extraction_method_counts``. If more than 20% of signatures use heuristics, the run is marked with a ``DEGRADED SEMANTIC QUALITY`` warning and should not be used as a validated defect claim.

## NAT fails with ``VehicleToolInput is not defined``

RecallZero 0.2.2 fixes the plugin registration by supplying explicit Pydantic input schemas to ``FunctionInfo.from_fn`` and avoiding deferred annotations in the registration module. Reinstall the editable package after upgrading so the NAT entry point loads the patched module.

## Hosted NIM returns many HTTP 429 responses

RecallZero 0.3.2 treats 429 as a transient capacity/rate-limit condition rather than a semantic extraction failure. The default hosted-NIM behavior is:

```text
LLM concurrency: 2
retry budget:    7
backoff:         Retry-After when supplied, otherwise exponential + jitter
signature cache: checkpoint successful ODI results incrementally
transient fallback: disabled
```

A healthy retry sequence may show temporary 429 warnings followed by HTTP 200 responses. If the retry budget is exhausted, the analysis fails instead of silently converting the remaining records with heuristics. Rerun the same command: completed NIM signatures are already cached and only unfinished/heuristic ODI records are retried.

If you deliberately prefer availability over semantic consistency, set:

```bash
export RECALLZERO_TRANSIENT_NIM_FALLBACK=true
```

Do not use that mode for a headline historical validation result unless the mixed extraction rate is explicitly reported and reviewed.

## Analysis still produces one giant cluster

0.3.2 performs component-family → canonical defect-family → DBSCAN clustering with complete-link refinement and records `clustering_diagnostics`. Inspect:

- component-group counts;
- canonical defect-family groups and DBSCAN clusters/noise by component;
- `largest_cluster_share`;
- sampled cosine-distance summaries;
- failure-mode purity.

A suspicious all-in-one result is marked `semantic_quality=DEGRADED` and emits a `CLUSTER QUALITY WARNING`. Do not run a headline Time Machine claim from a degraded snapshot.

## NAT 1.8 rejects `thinking` or loops in ReAct parsing

The modern `configs/aiq/recallzero_agent.yml` in 0.3.2 uses NAT's documented `tool_calling_agent` fields and deliberately has **no `thinking` YAML key**. Thinking is disabled only inside RecallZero's direct structured-extraction HTTP request when needed; that request option is separate from NAT's LLM configuration schema.

Verify:

```bash
nat --version
nat run --config_file configs/aiq/recallzero_agent.yml \
  --input "Investigate emerging safety concerns for the 2021 and 2022 Ford Mustang Mach-E."
```

## Complaint preflight returns HTTP 400 or zero eligible complaints for a known active model

RecallZero 0.3.5.post1 treats NHTSA complaint vehicle addressing as an input-integrity problem rather than evidence that the vehicle has no complaints. The complaint adapter now queries the NHTSA complaint product catalog with `issueType=c`, resolves conservative marketed-model family variants, fetches each accepted variant, and de-duplicates the union by ODI number.

Examples that motivated the adapter revision include generic marketed names whose ODI complaint records can be partitioned into configuration variants. Preflight JSON now records:

```text
complaint_adapter_revision
complaint_models_requested
complaint_models_resolved
complaint_models_queried
complaint_count_by_model_variant
```

After upgrading from 0.3.5, force one fresh preflight so stale normalized complaint caches cannot hide the new resolver:

```bash
recallzero benchmark-preflight \
  --manifest config/candidates.yml \
  --freeze benchmarks/detector_freeze_v1.yaml \
  --split validation \
  --refresh \
  --json data/runs/detector_v1_validation_preflight.json
```

Do not create the validation lock until preflight reports `READY FOR VALIDATION`.

## `pytest` fails in NAT with `NameError: VehicleToolInput is not defined`

In 0.3.5, the NAT compatibility test dynamically created a function with plain `exec()` inside a test module that has `from __future__ import annotations`. Python inherited that future flag, so the test function carried the string annotation `"VehicleToolInput"`. NAT 1.8 then synthesized a streaming wrapper in its own module and attempted to resolve that string in the wrong globals, producing the reported `NameError`.

The production `src/recallzero/aiq/register.py` deliberately does **not** enable deferred annotations, so its nested tool functions carry concrete runtime Pydantic classes. 0.3.5.post1 fixes the regression fixture by compiling it with `dont_inherit=True`, matching the production registration semantics. No detector logic or NAT YAML behavior changes are involved.


## Local NIM returns HTTP 200 but structured extraction is invalid

If logs show `NIM returned an invalid structured extraction` with fields such as `failure_mode: null`, the service is reachable; the failure is the semantic output contract rather than transport availability. RecallZero 0.3.5.post2 sends an explicit non-empty JSON-schema constraint, requests exactly one NIM-only repair, and then raises `NIMStructuredExtractionError` if the repair still fails.

For Detector v1 validation, do not enable heuristic fallback and do not lower `minimum_nim_fraction`. Clear or isolate the signature cache, rerun a smoke case, and require all signatures to report `extraction_method=nim` before recreating the validation lock.
