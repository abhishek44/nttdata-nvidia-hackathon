# RecallZero API

Start the server:

```bash
recallzero serve --host 0.0.0.0 --port 8080
```

Interactive OpenAPI documentation is available at `/docs`.

## `GET /health`

Returns service version, data directory, and whether NIM was requested/configured.

## `GET /api/v1/demo`

Runs a completely offline synthetic analysis and Time Machine replay. It is intended for installation verification only.

## `POST /api/v1/ingest`

```json
{
  "make": "FORD",
  "model": "MUSTANG MACH-E",
  "model_years": [2021, 2022],
  "refresh": false
}
```

Returns normalized complaint/recall counts and complaint date range.

## `POST /api/v1/analyze`

```json
{
  "make": "FORD",
  "model": "MUSTANG MACH-E",
  "model_years": [2021, 2022],
  "cutoff_date": "2022-06-09",
  "refresh": false,
  "use_nim": true,
  "max_complaints": null
}
```

Returns the complete `AnalysisRun`, including clusters, trends, recall matches, risk factor contributions, evidence records, method provenance, and warnings.

## `POST /api/v1/backtests`

```json
{
  "make": "FORD",
  "model": "MUSTANG MACH-E",
  "model_years": [2021, 2022],
  "target_campaign_number": "22V412000",
  "official_recall_date": "2022-06-10",
  "replay_start_date": null,
  "refresh": false,
  "use_nim": true,
  "alert_threshold": 75,
  "minimum_evidence": 4
}
```

Returns `BacktestResult`, including weekly frozen snapshots, first matching alert, lead time, and anti-leakage checks.

## `GET /api/v1/runs/{run_id}`

Returns a persisted analysis or backtest by identifier.

## `GET /api/v1/evidence/{odi_number}`

Returns the original normalized complaint and retained raw payload for one cached ODI identifier.

## `GET /api/v1/runs/{run_id}/signals/{signal_id}/brief`

Returns a deterministic Markdown engineering brief and evidence-critic result.

## Error semantics

- `404`: run, signal, complaint, or target campaign not found.
- `422`: request validation failure.
- `502`: upstream NHTSA/NIM or analysis execution failure.

## Production hardening

The bundled API is an engineering prototype. Before broader deployment:

- Add identity, authorization, and request audit logging.
- Restrict CORS.
- Apply rate limiting and bounded job queues.
- Move long analyses/backtests to asynchronous workers.
- Store evidence in a governed database/object store.
- Redact sensitive narrative content.
- Add upstream schema monitoring and circuit breakers.

## Live presentation endpoints

The following endpoints are presentation helpers around the frozen detector. They do not modify Detector v1 scoring.

### `GET /api/v1/demo/readiness`

Reports whether the local NIM, NVIDIA embedding endpoint, and cached demo ODI are ready for the fresh-inference demonstration.

### `POST /api/v1/demo/nvidia-trace`

```json
{
  "odi_number": "11466150"
}
```

Runs one fresh NIM structured extraction and one fresh NVIDIA embedding for a cached real complaint. The endpoint deliberately avoids signature-cache writes and detector risk recalculation.

### `GET /api/v1/demo/action-readiness`

Reports optional business-action integrations:

- alert webhook configured or off;
- NeMo Agent Toolkit available or missing;
- agent execution enabled or safe-off;
- tool catalog and configured agent model.

No secret webhook URL is returned.

### `POST /api/v1/demo/investigation-brief`

Accepts one persisted `DefectSignal` under `signal` and returns a deterministic Engineering Investigation Packet. It copies the existing detector risk values, summarizes traceable evidence, adds a fixed workflow recommendation, and renders the existing evidence-critic engineering brief. It does not call an LLM or recalculate risk.

### `POST /api/v1/demo/send-alert`

Accepts one persisted `DefectSignal`, rebuilds the investigation packet server-side, and sends an optional evidence-grounded summary to the configured webhook. Delivery is disabled when `RECALLZERO_DEMO_ALERT_WEBHOOK_URL` is unset.

### `POST /api/v1/demo/agent-investigate`

Runs the existing `configs/aiq/recallzero_agent.yml` NeMo Agent Toolkit `tool_calling_agent` workflow. This endpoint is fail-closed and disabled unless `RECALLZERO_DEMO_ENABLE_AGENT=true`. The subprocess uses an argument list rather than a shell, limits prompt length, and enforces a 180-second timeout.
