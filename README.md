# RecallZero MVP

This repository is the first implementation slice of the hackathon design: real NHTSA ingestion, complaint normalization, semantic clustering, deterministic trend/risk scoring, and a leakage-safe historical "Recall Time Machine" backtest.

## Project layout

```text
BE/  Backend API, pipeline, tests, scripts, and candidate data
FE/  Streamlit dashboard connected to the backend API
```

## What is implemented now

- Public NHTSA complaints API client by make/model/model-year.
- Public NHTSA recalls API client and campaign lookup.
- Complaint -> structured failure-signature extraction.
  - NVIDIA NIM LLM path when configured.
  - Small deterministic heuristic fallback for local development/tests.
- Semantic complaint clustering.
  - NVIDIA NeMo Retriever Embedding NIM path when configured.
  - TF-IDF fallback locally.
- Trend metrics: recent complaint rate, preceding baseline, acceleration, persistence.
- Explainable deterministic risk score.
- Time Machine replay that strictly excludes complaints on/after the official recall date.
- FastAPI skeleton for live product endpoints.
- First candidate historical recall: **Ford Mustang Mach-E / NHTSA 22V-412**.

## Why 22V-412 is only a candidate

NHTSA documentation says Ford issued recall 22V-412 on June 10, 2022 for 2021-2022 Mustang Mach-E high-voltage battery contactors that could overheat; an open contactor while driving can cause immediate loss of motive power. We still need to fetch the complaints that were filed *before* June 10, 2022 and measure whether a meaningful precursor cluster existed. We should not claim an early-warning lead time until that backtest is run.

## Run locally

### Backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r BE/requirements.txt
pip install -e .
cp BE/.env.example BE/.env
pytest -q
python -m recallzero.cli demo-backtest
uvicorn recallzero.api:app --reload
```

### Frontend

```bash
pip install -r FE/requirements.txt
python -m streamlit run FE/app.py
```

The frontend expects the backend at `http://localhost:8000` by default. You can change it with `RECALLZERO_API_URL`.

## Fetch real NHTSA complaints

```bash
python -m recallzero.cli fetch \
  --make Ford \
  --model "Mustang Mach-E" \
  --year 2021 \
  --out BE/data/mach_e_2021_complaints.json
```

Repeat for 2022. This requires internet access to `api.nhtsa.gov`.

## NVIDIA NIM integration

NVIDIA NIM for LLMs exposes an OpenAI-compatible `/v1/chat/completions` endpoint. NeMo Retriever Embedding NIM exposes `/v1/embeddings`. Configure the URLs/models in `.env`; the pipeline will switch from development fallbacks to NIM automatically.

Example configuration shape:

```dotenv
NVIDIA_NIM_LLM_URL=http://your-llm-nim:8000/v1
NVIDIA_NIM_LLM_MODEL=<served-model-name>
NVIDIA_NIM_EMBED_URL=http://your-embedding-nim:8000/v1
NVIDIA_NIM_EMBED_MODEL=<served-embedding-model-name>
NVIDIA_API_KEY=<only if your endpoint requires it>
```

## Data / trust rules

1. The LLM extracts language; it does **not** calculate the risk score.
2. Counts, dates, trend acceleration, persistence and lead time are deterministic.
3. Every complaint preserves its ODI number and raw NHTSA payload for evidence drill-down.
4. In a Time Machine run, future complaints and the future recall are excluded from the analysis window.
5. A complaint cluster is an early-warning signal, not proof that a defect exists.

## Next implementation slice

1. Fetch and cache 2021 + 2022 Mustang Mach-E complaints.
2. Filter to records filed before 2022-06-10.
3. Run NIM extraction over those records.
4. Cluster failure signatures and identify the cluster most similar to the 22V-412 failure description.
5. Replay week-by-week and record first threshold crossing.
6. Repeat across 3-5 historical recalls plus negative controls.
7. Only then wire the validated signals into the RecallZero dashboard.

## Run the first real historical candidate

After configuring NVIDIA NIM (recommended) and from a machine with NHTSA internet access:

```bash
python BE/scripts/backtest_candidate.py BE/data/candidates/22V412000.json \
  --out BE/data/22V412000_backtest.json
```

The runner deliberately performs **detection before it looks at the historical recall description**:

```text
pre-recall complaints
        -> extraction
        -> clustering
        -> trend/risk alerts
        -> freeze results
        -> post-hoc match to known recall for evaluation only
```

This avoids leaking the answer from the future recall into the detector.
