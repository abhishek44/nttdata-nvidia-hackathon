# RecallZero v2 Release Validation

Release version: **0.3.5.post1** — pre-validation complaint-input maintenance patch.

## Scope and freeze guarantee

Detector Freeze v1 remains the **0.3.3a2 detector algorithm state**. This maintenance patch was made before the validation cohort was locked or scored after live preflight exposed NHTSA complaint-addressing failures for Tesla Model Y and Ford F-150 cases.

Compared with 0.3.5, `config/risk.yml` is byte-identical. Of the 15 files historically grouped under `detector_modules`, 14 are byte-identical and one intentionally changes: `src/recallzero/data/nhtsa_client.py`, the NHTSA input adapter. The scoring/representation modules remain byte-identical: extractor/embeddings/NIM, taxonomy, clustering, severity, trend, risk, `pipeline.py` meta construction, recall matcher, config, normalizer, repository, and utilities. Production NAT registration (`src/recallzero/aiq/register.py`) is also unchanged.

The freeze manifest records:

- `detector_source_release: 0.3.3a2`
- `benchmark_release: 0.3.5.post1`
- `input_adapter_revision: nhtsa-complaint-catalog-v2`

Preflight requires raw complaint-cache provenance to match the frozen input-adapter revision. A stale 0.3.5 cache therefore fails preflight and instructs the operator to rerun with `--refresh`.

## NHTSA complaint input-adapter correction

The complaint endpoint can partition one marketed vehicle family across product-catalog model variants. 0.3.5.post1 resolves the NHTSA complaint product catalog (`issueType=c`) before complaint retrieval, then:

1. accepts exact punctuation-insensitive model identity;
2. accepts a broader catalog variant only when requested model tokens are a complete prefix of catalog model tokens;
3. never uses arbitrary substring or fuzzy matching;
4. queries every accepted catalog variant;
5. de-duplicates the union by ODI number;
6. records requested/resolved/queried/successful/rejected variants and per-variant counts in `recallzeroQuery` raw provenance;
7. tolerates HTTP 400 only as a rejected model address while other resolved variants remain available;
8. fails hard if every resolved variant is rejected or on non-400 HTTP failures;
9. retains HTTP 200 empty partitions as legitimate empty results.

Benchmark preflight now surfaces this query provenance and refuses to become ready when cached complaint input lacks the frozen adapter revision.

## NAT compatibility regression-test correction

The reported NAT test failure was reproduced conceptually as a test-fixture annotation problem. The test module enables postponed annotations; its plain `exec()` inherited that compiler state, so the dynamically-created tool carried the string annotation `"VehicleToolInput"`. NAT 1.8's generated wrapper then attempted to resolve that name outside the fixture's namespace and raised `NameError`.

Production `src/recallzero/aiq/register.py` does not enable postponed annotations and uses concrete Pydantic schema classes at runtime. 0.3.5.post1 therefore changes the regression fixture, not production registration: it compiles the dynamic function with `dont_inherit=True` and asserts that the resulting annotation is the concrete `VehicleToolInput` class before calling `FunctionInfo.from_fn`.

## Validated in the build environment

- `PYTHONPATH=src pytest -q -ra`: **90 collected, 89 passed, 1 skipped**.
- The skipped test is the optional NAT-runtime `FunctionInfo.from_fn` integration test because `nat` is not installed in the build container.
- The non-NAT eager-annotation regression assertion passes.
- `python -m compileall -q src tests`: **PASS**.
- `PYTHONPATH=src python -m recallzero.cli --version`: **RecallZero 0.3.5.post1**.
- `freeze-verify --manifest benchmarks/detector_freeze_v1.yaml`: **PASS** for frozen source/evaluation hashes, active `risk.yml`, and live Settings-loaded risk/clustering/trend/model/execution values.
- Targeted complaint-adapter/benchmark/NAT fixture tests pass.
- Ruff is not installed in the build container; no Ruff result is claimed.
- Wheel build completed offline using the installed build toolchain.
- Isolated-target wheel installation reports **0.3.5.post1**.
- Wheel SHA-256: `05b59a7f6df37f3213f710618be361dbb4b7b47e9bd6824bab7d740eaa816b0a`.

## New regression coverage

Complaint data adapter tests cover:

- Model Y generic/family lookup where one model address returns HTTP 400 and valid catalog variants return complaints;
- F-150 family aggregation across base/SUPERCAB/SUPERCREW catalog entries while excluding F-250;
- ODI de-duplication across complaint partitions;
- token-prefix family protection (`500` does not absorb `500X`, Model Y does not absorb Model S, F-150 does not absorb F-250);
- hard failure when all resolved variants return HTTP 400;
- valid HTTP 200 empty population behavior;
- Mach-E punctuation alias resolution without drifting into unrelated Mustang catalog models.

Benchmark regression tests additionally cover:

- surfacing complaint model-resolution provenance in preflight;
- rejecting stale raw complaint input that lacks `nhtsa-complaint-catalog-v2` provenance and requiring `--refresh`.

## Source comparison against 0.3.5

Intentional source changes that can affect runtime behavior are limited to:

- `src/recallzero/data/nhtsa_client.py` — input-addressing/aggregation revision;
- `src/recallzero/benchmark.py` — preflight provenance and frozen adapter-revision enforcement;
- `src/recallzero/cli.py` — preflight query diagnostics.

Tests, documentation, version metadata, and the freeze manifest also change. Production NAT registration remains unchanged; only its compatibility test fixture changes.

## Requires live validation on the target GB10 environment

The build container does not have live NHTSA/NVIDIA benchmark connectivity and does not contain NAT, so it cannot claim that the two originally failing live cases are resolved end to end. The adapter behavior is regression-tested with deterministic HTTP mocks; the target GB10 preflight is the authoritative live confirmation.

After upgrading, run:

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
```

Do **not** create the validation lock unless the refreshed preflight ends with `READY FOR VALIDATION`.

For the Tesla Model Y and Ford F-150 cases, inspect the new fields:

- `complaint_adapter_revision`
- `complaint_models_requested`
- `complaint_models_resolved`
- `complaint_models_queried`
- `complaint_count_by_model_variant`

If a live case still fails, treat that as an input/metadata problem and resolve it before locking; do not modify Detector v1 scoring based on preflight.

## Deferred known gaps

- **TAX-001:** `consequence_family == OTHER` can bypass taxonomy consistency checks and may fragment one real phenomenon across multiple lineage IDs.
- **META-001:** mechanism meta-signals may over-aggregate evidence in mature populations.

Both remain intentionally unfixed through 0.3.5.post1.

## Non-claims

- 0.3.5.post1 does not claim Detector v1 has passed generalization validation.
- The live 20-case validation cohort has not been scored by the build environment.
- Mock complaint retrieval tests are adapter correctness tests, not evidence about actual complaint counts.
- `UNCONFIRMED_ALERT` remains an operational burden category, not proof that a safety concern is false.
