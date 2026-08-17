# GB10 Runbook

## 1. Create the project environment

```bash
cd recallzero_v2
./scripts/gb10_setup.sh
source .venv/bin/activate
```

The script prefers Python 3.12. Override it with:

```bash
PYTHON_BIN=python3.11 ./scripts/gb10_setup.sh
```

## 2. Configure NVIDIA access

```bash
cp -n .env.example .env
$EDITOR .env
```

Hosted inference requires `NVIDIA_API_KEY`. For local NIMs, set `RECALLZERO_NIM_BASE_URL` to the chat endpoint and, when embeddings are served separately, set `RECALLZERO_EMBEDDING_BASE_URL` to the embedding endpoint. Both values use OpenAI-compatible `/v1` bases.

## 3. Validate layers independently

```bash
recallzero doctor
recallzero doctor --probe-nim
recallzero demo
pytest
```

Then validate live NHTSA separately:

```bash
recallzero fetch --make FORD --model "MUSTANG MACH-E" --years 2021,2022 --refresh
```

Then validate NIM extraction on a bounded sample:

```bash
recallzero analyze \
  --make FORD \
  --model "MUSTANG MACH-E" \
  --years 2021,2022 \
  --cutoff 2022-06-09 \
  --max-complaints 25
```

Review `extraction_method_counts`, `semantic_quality`, and the clustering summary. With 0.3.1, configured hosted-NIM 429/5xx failures are retried and do not silently become heuristic signatures by default.

## 4. Run the full experiment

```bash
./scripts/run_mach_e_backtest.sh
```

Do not report a lead-time claim until raw records, failure clusters, target matching, configuration, and all anti-leakage checks are manually reviewed.

## 5. Launch interfaces

```bash
recallzero serve --port 8080
./scripts/run_aiq.sh "Investigate the 2021 and 2022 Ford Mustang Mach-E."
```

## 6. GB10/local model optimization sequence

1. Establish a correct hosted-NIM baseline.
2. Export a fixed complaint sample and expected structured-output schema.
3. Deploy candidate local chat and embedding NIMs.
4. Point environment variables to local endpoints.
5. Compare extraction validity, fallback rate, cluster stability, latency, and memory use.
6. Change one model/parameter at a time.
7. Re-run historical positives and negative controls before accepting a model change.

## 7. Operational checkpoints

- Free disk space for raw/cached data.
- API quota and transient error rates.
- NHTSA schema changes.
- NIM model availability/version changes.
- Percentage of heuristic extraction fallbacks.
- Number and size of DBSCAN noise clusters.
- Alert volume per vehicle/time period.
- Backtest anti-leakage status.

## 8. Upgrade an existing 0.2.x / 0.3.0 checkout

Replace the source files with the 0.3.1 archive, retain your existing `.env` and `data/` directory, then reinstall the editable package:

```bash
cd recallzero_v2
source .venv/bin/activate
python -m pip install -e .
hash -r
recallzero --version
```

Expected output:

```text
RecallZero 0.3.1
```

Retaining `data/cache/*_signatures.json` is useful: successful NIM signatures from 0.2.2/0.3.0 can be reused, while heuristic fallback entries are automatically retried when NIM is enabled. In 0.3.1, severity validation, canonical defect taxonomy, clustering refinement, recall matching, and backtest status semantics are recomputed at analysis time, so rerun analysis/backtests rather than reusing prior run JSON as evidence.
