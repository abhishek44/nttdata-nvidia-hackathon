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
