# RecallZero v2

**Evidence-grounded vehicle-defect early warning, recall-gap analysis, and leakage-safe historical backtesting.**

RecallZero reads public NHTSA owner complaints, normalizes varied language into failure signatures, groups semantically similar reports, measures trend/persistence/severity with deterministic analytics, checks recalls visible at the analysis cutoff, and produces traceable engineering triage signals. The included **Recall Time Machine** replays only pre-recall evidence and measures whether a matching alert appeared before a known campaign.

This repository is a clean rebuild based on the supplied RecallZero design documents. It includes:

- Public NHTSA complaint and recall ingestion with local caching.
- Typed Pydantic evidence contracts that preserve original ODI complaint identifiers and raw payloads.
- NVIDIA NIM failure-signature extraction with a deterministic heuristic fallback.
- NVIDIA embedding support with TF-IDF fallback, component/consequence child clustering, root-mechanism meta-signals, and DBSCAN/cosine complete-link refinement.
- Deterministic 28-day recent vs. 84-day baseline trend analytics.
- Explainable risk scoring: 30% severity, 25% trend, 15% persistence, 20% evidence, 10% recall gap.
- Leakage-safe weekly historical replay and post-hoc recall matching.
- Deterministic evidence critic and engineering brief renderer.
- FastAPI service and lightweight Safety Radar dashboard.
- NVIDIA NeMo Agent Toolkit integration (`nat`, formerly AIQ Toolkit), legacy `aiq` compatibility, and a patch utility for a stock AI-Q Blueprint configuration.
- Unit tests and an entirely offline synthetic demonstration.

> RecallZero identifies **signals for engineering review**. It does not establish that a defect exists, prove causation, or estimate an exposure-adjusted failure rate.

## Architecture


### Detector Freeze v1 and 0.3.4 benchmark phase

Detector Freeze v1 is the 0.3.3a2 detection state. RecallZero 0.3.4 intentionally adds benchmark/freeze-verification infrastructure without changing detector math or eligibility rules. The 75-point alert threshold, 30/25/15/20/10 risk weights, 28/84-day windows, DBSCAN parameters, severity validator, taxonomy, meta construction, recall matcher, and anti-leakage rules are frozen.

The freeze manifest hashes detector-critical modules (including `analytics/severity.py`, `analytics/risk_engine.py`, `pipeline.py`, and `recall/matcher.py`) and verifies the *live loaded* risk/clustering/trend/model settings. Snapshot top candidates now persist complete factor score/weight/contribution/explanation data for forensic benchmark comparison. Deferred TAX-001 and META-001 issues are documented under `benchmarks/KNOWN_GAPS.md`; they are not silently fixed against the Mach-E development case.

### 0.3.3a scientific slice

0.3.3a1 repaired moving-event severity false negatives and added the first small scientific slice. 0.3.3a2 then wired the parked-event veto so parked/no-start narratives no longer inherit unrelated motion text while genuine moving shutdowns remain valid. The Mach-E outcome was rerun with threshold/weights/windows/clustering frozen before declaring Detector Freeze v1.

### 0.3.2 signal model

Each complaint is represented on two independent axes: `failure_mechanism` captures root-oriented language such as a high-voltage junction-box/contactor problem, while `consequence_family` captures what the driver experienced such as loss of motive power or a no-start condition. Fine-grained child clusters remain available for ODI drill-down. When the same specific mechanism is split across child clusters, components, or consequences, RecallZero also creates a deterministic `meta` signal that preserves the contributing child-cluster IDs and complaint IDs.

Time Machine snapshots record stable `lineage_id` values, the highest risk at each cutoff, distance to the unchanged 75-point alert threshold, and up to five frozen top candidates. Only after a snapshot is frozen is the known target recall used for evaluation-only `posthoc_target_score` diagnostics.


```text
NHTSA complaints + recalls
          |
          v
Typed normalization and immutable evidence cache
          |
          v
Failure signature extraction
NVIDIA NIM LLM -> deterministic fallback for non-transient failures
          |
          v
Semantic representations
NVIDIA embeddings -> TF-IDF fallback for non-transient failures
          |
          v
NHTSA component-family partition
          |
          v
Canonical defect-family normalization
          |
          v
Child clusters + root-mechanism meta-signals + diagnostics
          |
          v
Deterministic trend + severity + risk
          |
          +----------------------+
          |                      |
          v                      v
Visible-recall matching     Recall Time Machine
(current cutoff only)       (historical weekly replay)
          |                      |
          +----------+-----------+
                     v
       Evidence critic + engineering brief
                     |
                     v
       FastAPI / dashboard / NAT-AIQ tools
```

The trust boundary is deliberate:

- **GenAI:** interprets and normalizes complaint language and can explain an already-calculated result.
- **Deterministic code:** counts evidence, computes dates/windows, trend, persistence, severity factors, risk, recall lead time, and anti-leakage checks.

See [Architecture](docs/ARCHITECTURE.md), [Backtest Protocol](docs/BACKTEST_PROTOCOL.md), [Benchmark Protocol](docs/BENCHMARK_PROTOCOL.md), and [Source Traceability](docs/SOURCE_TRACEABILITY.md).

## Requirements

- Python 3.11, 3.12, or 3.13. Python 3.12 is recommended on GB10/DGX Spark.
- Internet access for live NHTSA and hosted NVIDIA APIs.
- `NVIDIA_API_KEY` for hosted NIM inference. A local OpenAI-compatible NIM endpoint can be used without the hosted key.
- Optional: NVIDIA NeMo Agent Toolkit for the `nat` workflow. The deprecated `aiq` module is not required for current AI-Q Blueprint integration.

## Quick start on GB10

```bash
unzip RecallZero_v2_GB10_AIQ.zip
cd recallzero_v2

./scripts/gb10_setup.sh
source .venv/bin/activate
cp -n .env.example .env
# Edit .env and set NVIDIA_API_KEY=nvapi-...

recallzero doctor --probe-nim
recallzero demo
```

`gb10_setup.sh` uses `uv` when available and otherwise falls back to `python3.12 -m venv` plus `pip`. By default it installs the core, development dependencies, and current NeMo Agent Toolkit integration.

To skip NeMo Agent Toolkit during initial setup:

```bash
INSTALL_AIQ=0 ./scripts/gb10_setup.sh
```

## Hosted or local NVIDIA inference

Hosted NVIDIA API:

```dotenv
NVIDIA_API_KEY=nvapi-...
RECALLZERO_NIM_BASE_URL=https://integrate.api.nvidia.com/v1
RECALLZERO_LLM_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
RECALLZERO_EMBEDDING_MODEL=nvidia/nv-embedqa-e5-v5
```

OpenAI-compatible local NIM endpoints can be selected without changing application code:

```dotenv
NVIDIA_API_KEY=
RECALLZERO_NIM_BASE_URL=http://127.0.0.1:8000/v1
RECALLZERO_EMBEDDING_BASE_URL=http://127.0.0.1:8001/v1
RECALLZERO_LLM_MODEL=<model exposed by local chat endpoint>
RECALLZERO_EMBEDDING_MODEL=<model exposed by local embedding endpoint>
```

If NIM is not configured, the pipeline can run with heuristic extraction plus TF-IDF. If a configured hosted/local NIM returns invalid non-transient output, the deterministic fallback remains available. **Transient hosted failures such as exhausted HTTP 429/502/503 retries do not silently fall back by default in 0.3.2**, because mixing extraction methods can corrupt an experimental clustering result. Set `RECALLZERO_TRANSIENT_NIM_FALLBACK=true` only when availability is more important than semantic consistency.

Successful NIM signatures are cached and checkpointed incrementally. Re-running a vehicle analysis reuses completed ODI signatures for the same model and retries only missing/heuristic records when NIM is enabled.

## Run the offline demonstration

No network or API key is required:

```bash
recallzero demo
```

The synthetic result is only a functional smoke test. It must not be presented as a real measured recall lead-time result.

## Fetch real NHTSA data

```bash
recallzero fetch \
  --make FORD \
  --model "MUSTANG MACH-E" \
  --years 2021,2022
```

NHTSA can use different punctuation for the same model across its complaint and recall catalogs. RecallZero resolves the exact recall-catalog spelling automatically—for example, `MUSTANG MACH-E` to `MUSTANG MACH E`—before requesting recalls.

Cached evidence is written under:

```text
data/raw/          original API responses
data/normalized/   typed complaints and recalls
data/cache/        extracted failure signatures
data/runs/         analysis and backtest results
```

## Investigate a vehicle

Hosted/local NIM when configured, deterministic fallback otherwise:

```bash
recallzero analyze \
  --make FORD \
  --model "MUSTANG MACH-E" \
  --years 2021,2022 \
  --cutoff 2022-06-09 \
  --brief \
  --json data/runs/mach_e_pre_recall_analysis.json
```

Force a fully offline language-understanding path:

```bash
recallzero analyze \
  --make FORD \
  --model "MUSTANG MACH-E" \
  --years 2021,2022 \
  --no-nim
```

## Run the Recall Time Machine

The recovered design documents identify Ford Mustang Mach-E campaign `22V412000`, with an official boundary of `2022-06-10`, as an **unvalidated candidate**. Run it as an experiment, not as a claimed success:

```bash
./scripts/run_mach_e_backtest.sh
```

Equivalent command:

```bash
recallzero backtest \
  --make FORD \
  --model "MUSTANG MACH-E" \
  --years 2021,2022 \
  --campaign 22V412000 \
  --recall-date 2022-06-10 \
  --json data/runs/mach_e_22V412000_result.json
```

Valid outcomes include `EARLY_SIGNAL_DETECTED`, `EARLY_ALERT_TARGET_UNMATCHED`, `NO_EARLY_SIGNAL`, and `INVALID_BACKTEST`. `EARLY_ALERT_TARGET_UNMATCHED` means a pre-recall risk-qualified alert existed, but the post-hoc target-recall matcher did not qualify it; this is distinct from no alert existing at all. Do not tune thresholds after seeing a target outcome and then report the same case as an unbiased validation.

## Detector freeze and benchmark

Verify that the current checkout and *live runtime configuration* still match Detector Freeze v1:

```bash
recallzero freeze-verify \
  --manifest benchmarks/detector_freeze_v1.yaml
```

The verifier hashes detector/evaluation modules, hashes the active `risk.yml`, and compares values loaded through `Settings().risk_config()`, `ClusteringConfig`, `TrendConfig`, and the configured model identifiers. A config-only edit therefore fails the freeze even if the static manifest numbers were not edited.

Run the candidate manifest:

```bash
recallzero benchmark \
  --manifest config/candidates.yml \
  --freeze benchmarks/detector_freeze_v1.yaml \
  --json data/runs/benchmark_v1.json \
  --csv data/runs/benchmark_v1.csv
```

`config/candidates.yml` preserves the existing `name/make/model/model_years/campaign_number/official_recall_date/status/note` shape and adds `expected_role` (`positive` or `negative`) plus `benchmark_split` (`development` or `holdout`). Targetless negative controls use an exclusive `evaluation_end_date` instead of a campaign. Positive target metadata is used only after detector snapshots are frozen.

Every persisted top candidate contains the complete deterministic risk-factor breakdown (`severity`, `trend`, `persistence`, `evidence`, and `recall_gap`) including score, weight, contribution, and explanation. The aggregate output reports positive sensitivity/lead-time plus negative false-alert snapshot and unique-lineage burden. `unique false lineages` must be interpreted with the TAX-001 caveat in `benchmarks/KNOWN_GAPS.md`, because taxonomy-driven lineage fragmentation can inflate that metric.

Compare two benchmark runs without modifying the detector:

```bash
recallzero benchmark-compare \
  data/runs/benchmark_v1.json \
  data/runs/benchmark_v2.json \
  --json data/runs/benchmark_compare.json
```

## API and Safety Radar dashboard

```bash
recallzero serve --host 0.0.0.0 --port 8080
```

Open `http://<GB10-host>:8080/` and API docs at `http://<GB10-host>:8080/docs`.

Useful endpoints:

```text
GET  /health
GET  /api/v1/demo
POST /api/v1/ingest
POST /api/v1/analyze
POST /api/v1/backtests
GET  /api/v1/runs/{run_id}
GET  /api/v1/evidence/{odi_number}
GET  /api/v1/runs/{run_id}/signals/{signal_id}/brief
```

See [API Reference](docs/API.md) and [Troubleshooting](docs/TROUBLESHOOTING.md).

## NeMo Agent Toolkit / AIQ integration

NVIDIA renamed the general-purpose AIQ Toolkit to **NeMo Agent Toolkit**. Current workflows use the `nat` CLI; a legacy `aiq` registration and configuration are also included.

### Standalone RecallZero agent

```bash
source .venv/bin/activate
./scripts/run_aiq.sh \
  "Investigate emerging safety concerns for the 2021 and 2022 Ford Mustang Mach-E."
```

The modern configuration is `configs/aiq/recallzero_agent.yml`. On NAT 1.8 it uses the native `tool_calling_agent` and exposes five deterministic tools:

- `recallzero_fetch_vehicle_data`
- `recallzero_analyze_vehicle`
- `recallzero_run_backtest`
- `recallzero_get_evidence`
- `recallzero_engineering_brief`

Direct command:

```bash
nat run \
  --config_file configs/aiq/recallzero_agent.yml \
  --input "Run a leakage-safe backtest for campaign 22V412000."
```

### Add RecallZero to the stock AI-Q Blueprint you already run

Activate the **same Python environment used by the stock AI-Q Blueprint**, then from this repository run:

```bash
./scripts/integrate_stock_aiq.sh \
  /path/to/aiq-blueprint/configs/config_cli_default.yml \
  /path/to/aiq-blueprint/configs/config_cli_recallzero.yml
```

The script installs RecallZero as an editable plugin in that environment and patches the selected YAML. The source config is never overwritten. For current AI-Q 2.x configurations, the patcher adds a default-enabled RecallZero entry to the central `data_source_registry` so agents keep their existing tool inheritance. For older configs or standalone agents, it safely extends explicit shallow/deep researcher or ReAct tool lists.

Manual equivalent:

```bash
python -m pip install -e /path/to/recallzero_v2
recallzero-aiq-patch stock.yml --output stock_with_recallzero.yml
```

Then launch the stock AI-Q Blueprint using its normal command and the patched config. Set an absolute data directory when the AI-Q process runs from another repository:

```bash
export RECALLZERO_DATA_DIR=/absolute/path/to/recallzero_v2/data
```

See [AIQ Integration](docs/AIQ_INTEGRATION.md).

## Tests and validation

```bash
pytest
ruff check src tests
python -m compileall -q src tests
```

The suite covers normalization, structured/heuristic extraction, transient NIM retry behavior, dual-axis taxonomy, meta-signal lineage, complete-link max-distance refinement, event-scoped severity provenance, structured recall matching, active-week persistence, NHTSA response parsing, campaign lookup, API health/demo, NAT/AI-Q config integration, and Time Machine anti-leakage/outcome semantics.

The 0.3.3a1 archive is validated offline before release; live NHTSA/NIM execution and the exact installed NAT runtime must still be smoke-tested on the GB10. See [Release Validation](RELEASE_VALIDATION.md).

## Risk configuration

Prototype defaults live in `config/risk.yml`:

```yaml
risk:
  weights:
    severity: 0.30
    trend: 0.25
    persistence: 0.15
    evidence: 0.20
    recall_gap: 0.10
  alert_threshold: 75.0
  minimum_evidence: 4
```

These are starting values, not validated production thresholds. Calibrate them with multiple historical positives and negative controls, and report precision, false-alert rate, lead-time distribution, and sensitivity to clustering parameters.

## Project layout

```text
recallzero_v2/
├── config/                 risk and candidate configuration
├── configs/aiq/            NAT, legacy AIQ, and stock Blueprint snippets
├── data/                   local evidence/cache/run directories
├── docs/                   architecture, protocol, AIQ, API, GB10 guidance
├── scripts/                setup, integration, agent and backtest launchers
├── src/recallzero/
│   ├── aiq/                custom NAT/AIQ function registrations
│   ├── analytics/          deterministic trend, severity, and risk
│   ├── api/                FastAPI routes
│   ├── backtest/           Recall Time Machine
│   ├── data/               NHTSA client, normalizer, file repository
│   ├── intelligence/       NIM extraction/embeddings and clustering
│   ├── investigation/      evidence critic and brief renderer
│   ├── models/             typed domain/API contracts
│   ├── recall/             visible-recall and target matching
│   ├── web/                Safety Radar UI
│   ├── cli.py
│   └── pipeline.py
└── tests/
```

## Security and operational notes

- Never commit `.env`, NVIDIA API keys, or production evidence exports.
- The development dashboard enables permissive CORS. Restrict origins and add authentication before exposing it outside a trusted network.
- Complaint narratives may contain sensitive free text. Apply retention, access-control, redaction, and audit policies appropriate to your organization.
- The normalized complaint model keeps at most an 11-character VIN prefix, while raw NHTSA cache files may retain the original payload, including a fuller VIN when supplied. Protect or redact raw evidence according to policy.
- NHTSA availability and response schemas should be monitored; cached raw payloads make normalization issues auditable.

See [Security](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
