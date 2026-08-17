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
