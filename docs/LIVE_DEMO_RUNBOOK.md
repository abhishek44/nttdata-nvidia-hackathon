# RecallZero Live Hackathon Demo Runbook

This presentation build leaves the frozen Detector v1 implementation unchanged. It adds a separate live-demo path:

- **NVIDIA NIM** on GB10 (`nvidia/nemotron-3.5-lightning-30b-a3b`) performs one fresh structured complaint extraction.
- **NVIDIA embeddings** (`nvidia/nv-embedqa-e5-v5`) performs one fresh semantic embedding.
- **RecallZero** exposes the existing Time Machine backtest and evidence APIs.
- **Streamlit** provides a guided three-screen demo: Live NVIDIA → Time Machine → Validation.

The main presentation should use the audited reference Time Machine artifact. The optional live quick replay is available to prove the backend path, but is not required for a successful demo.

## Port layout

Do not run RecallZero FastAPI on port 8000. The local LLM NIM already uses it.

| Service | Port |
|---|---:|
| Local NVIDIA LLM NIM | 8000 |
| RecallZero FastAPI | 8080 |
| Streamlit demo UI | 8501 |

NVIDIA embeddings remain on the configured hosted endpoint unless you deliberately change `RECALLZERO_EMBEDDING_BASE_URL`.

## Required environment

Use your existing `.env` and preserve the Detector v1 data/cache. Confirm these effective values:

```bash
RECALLZERO_USE_NIM=true
RECALLZERO_NIM_BASE_URL=http://127.0.0.1:8000/v1
RECALLZERO_EMBEDDING_BASE_URL=https://integrate.api.nvidia.com/v1
RECALLZERO_LLM_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
RECALLZERO_EMBEDDING_MODEL=nvidia/nv-embedqa-e5-v5
RECALLZERO_DATA_DIR=./data_detector_v1_localnim_post2
NVIDIA_API_KEY=<your NVIDIA API key for hosted embeddings>
```

Keep `RECALLZERO_LLM_CONCURRENCY=2`, `RECALLZERO_SIGNATURE_BATCH_SIZE=20`, and `RECALLZERO_TRANSIENT_NIM_FALLBACK=false` if they are already part of your frozen setup.

## Install

From the project root:

```bash
source .venv/bin/activate
python -m pip install -e '.[demo]'
```

## Verify the frozen detector

```bash
recallzero freeze-verify --manifest benchmarks/detector_freeze_v1.yaml
```

This must pass. The demo changes are outside the Detector v1 freeze modules.

## Check the local NVIDIA NIM

```bash
curl -s http://127.0.0.1:8000/v1/models | jq .
```

If the NIM is not running, start it using the exact container/profile you already validated on GB10. Do not change the model on presentation day.

## Start the RecallZero backend

Terminal 1:

```bash
source .venv/bin/activate
./scripts/run_demo_backend.sh
```

Backend URL: `http://127.0.0.1:8080`

Verify:

```bash
curl -s http://127.0.0.1:8080/health | jq .
curl -s http://127.0.0.1:8080/api/v1/demo/readiness | jq .
```

`ready_for_live_trace` should be `true`.

## Run preflight

Configuration-only check:

```bash
python scripts/demo_preflight.py
```

Full dress rehearsal, including one real NIM call and one real NVIDIA embedding call:

```bash
python scripts/demo_preflight.py --live
```

Run `--live` before the judges arrive, not repeatedly during setup.

## Start the Streamlit UI

Terminal 2:

```bash
source .venv/bin/activate
./scripts/run_demo_ui.sh
```

Open on the GB10 desktop:

```text
http://127.0.0.1:8501
```

From another machine on the same reachable network:

```text
http://<GB10-IP>:8501
```

If direct ports are blocked, tunnel only the Streamlit UI from your laptop. The Streamlit process calls FastAPI locally on GB10, so the browser does not need direct access to port 8080:

```bash
ssh -L 8501:127.0.0.1:8501 <user>@<gb10-host>
```

Then open `http://127.0.0.1:8501` locally. Add a separate `-L 8080:127.0.0.1:8080` only if you explicitly want to browse the FastAPI docs from your laptop.

## Presentation flow

1. **LIVE NVIDIA** — load ODI 11466150 and click **Run fresh NVIDIA inference**. Show the NIM model, structured signature, embedding model, 1024-dimensional vector, latency, and `cache_write = NO`.
2. **TIME MACHINE** — use the **audited Mach-E reference replay**. Move to the May 26 alert snapshot, show deterministic risk factors and ODI evidence, then click **Reveal historical outcome**.
3. **VALIDATION** — show the preregistered FAIL result: target-qualified sensitivity 1/10, positive vehicles with any alert 6/10, valid controls with unconfirmed alert 1/9, and anti-leakage/freeze checks passing.

The optional **Run live quick replay** button recomputes the Apr 7–Jun 9 historical window using the current GB10 backend. It uses cached NHTSA records and cached failure signatures but may call NVIDIA embeddings during clustering. If it is slow or unavailable, do not debug on stage; return to the audited reference replay.

## Demo-day fallback policy

- If the fresh NIM trace fails, show the readiness error and move to Time Machine. Do not claim the failed call was live.
- If the live quick replay fails, use the audited reference replay. This is a frozen research artifact, not fabricated demo data.
- Never clear the semantic cache or refresh NHTSA data immediately before the presentation.
- Never change the detector threshold, weights, taxonomy, or validation artifacts for the demo.
