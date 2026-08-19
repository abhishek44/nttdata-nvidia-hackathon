# RecallZero Live Hackathon Demo Runbook

This presentation build keeps the frozen Detector v1 implementation unchanged while adding business-facing demo capabilities around it:

- **NVIDIA NIM** on GB10 (`nvidia/nemotron-3.5-lightning-30b-a3b`) performs one fresh structured complaint extraction.
- **NVIDIA embeddings** (`nvidia/nv-embedqa-e5-v5`) performs one fresh semantic embedding.
- **RecallZero Time Machine** exposes the audited historical replay and evidence trail.
- **Engineering Action Center** converts an already-computed signal into a deterministic quality-engineering brief and downloadable evidence packet.
- **Optional proactive alerting** can post the evidence-grounded investigation summary to a configured webhook.
- **Optional NVIDIA NeMo Agent Toolkit** can orchestrate the existing RecallZero tools through the real `tool_calling_agent` workflow.
- **Streamlit** provides a guided four-screen demo: Live NVIDIA → Time Machine → Action Center → Validation.

The new demo/action paths do not change Detector v1 scores, thresholds, clustering, taxonomy, or the historical validation result.

## Port layout

Do not run RecallZero FastAPI on port 8000. The local LLM NIM already uses it.

| Service | Port |
|---|---:|
| Local NVIDIA LLM NIM | 8000 |
| RecallZero FastAPI | 8080 |
| Streamlit demo UI | 8501 |

NVIDIA embeddings remain on the configured hosted endpoint unless you deliberately change `RECALLZERO_EMBEDDING_BASE_URL`.

## Required environment

Preserve the data/cache used by the stable baseline and confirm these effective values:

```bash
RECALLZERO_USE_NIM=true
RECALLZERO_NIM_BASE_URL=http://127.0.0.1:8000/v1
RECALLZERO_EMBEDDING_BASE_URL=https://integrate.api.nvidia.com/v1
RECALLZERO_LLM_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
RECALLZERO_EMBEDDING_MODEL=nvidia/nv-embedqa-e5-v5
RECALLZERO_DATA_DIR=/absolute/path/to/data_detector_v1_localnim_post2
NVIDIA_API_KEY=<your NVIDIA API key for hosted embeddings>
```

Keep the frozen execution settings:

```bash
RECALLZERO_LLM_CONCURRENCY=2
RECALLZERO_SIGNATURE_BATCH_SIZE=20
RECALLZERO_TRANSIENT_NIM_FALLBACK=false
```

### Optional alert delivery

The Action Center always supports downloading the engineering brief and JSON evidence packet. Webhook delivery is optional:

```bash
RECALLZERO_DEMO_ALERT_WEBHOOK_URL=<secret webhook URL>
RECALLZERO_DEMO_ALERT_PROVIDER=slack   # slack | teams | generic
```

The UI intentionally never displays the secret webhook URL.

### Optional NVIDIA Investigator Agent

Keep this **off by default** until you have rehearsed it successfully:

```bash
RECALLZERO_DEMO_ENABLE_AGENT=false
```

To enable it after NAT is verified:

```bash
RECALLZERO_DEMO_ENABLE_AGENT=true
RECALLZERO_AGENT_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
RECALLZERO_AGENT_CONFIG=configs/aiq/recallzero_agent.yml
```

The agent orchestrates five existing RecallZero tools. It does not replace deterministic detector mathematics.

## Install

Recommended demo environment:

```bash
cd ~/projects/recallzero_v2
python3 -m venv .venv-demo
source .venv-demo/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[demo]'
```

If you want the optional NVIDIA Investigator Agent as well:

```bash
python -m pip install -e '.[demo,aiq]'
```

Do not install the AIQ/NAT extra immediately before presenting unless you have time to rehearse it.

## Verify the frozen detector

```bash
recallzero freeze-verify --manifest benchmarks/detector_freeze_v1.yaml
```

This must pass. The new demo/action files are outside the Detector v1 freeze modules.

## Check the local NVIDIA NIM

```bash
curl -s http://127.0.0.1:8000/v1/models | jq .
```

Use the same NIM container/profile already validated on GB10. Do not switch models on presentation day.

## Start RecallZero backend

Terminal 1:

```bash
cd ~/projects/recallzero_v2
source .venv-demo/bin/activate
./scripts/run_demo_backend.sh
```

Backend URL: `http://127.0.0.1:8080`

Verify:

```bash
curl -s http://127.0.0.1:8080/health | jq .
curl -s http://127.0.0.1:8080/api/v1/demo/readiness | jq .
curl -s http://127.0.0.1:8080/api/v1/demo/action-readiness | jq .
```

`ready_for_live_trace` should be `true`.

For the Action Center, it is completely acceptable for:

```text
alert.configured = false
agent.execution_enabled = false
```

The brief/download workflow still works with both optional integrations disabled.

## Preflight

Configuration-only check:

```bash
python scripts/demo_preflight.py
```

Full NVIDIA dress rehearsal:

```bash
python scripts/demo_preflight.py --live
```

Only after NAT has been installed, tested, and explicitly enabled should you run:

```bash
python scripts/demo_preflight.py --agent
```

or both live paths:

```bash
python scripts/demo_preflight.py --live --agent
```

Do this before judges arrive, not repeatedly during setup.

## Start Streamlit UI

Terminal 2:

```bash
cd ~/projects/recallzero_v2
source .venv-demo/bin/activate
./scripts/run_demo_ui.sh
```

Open on the GB10 desktop:

```text
http://127.0.0.1:8501
```

If using a laptop, tunnel only Streamlit:

```bash
ssh -L 8501:127.0.0.1:8501 <user>@<gb10-host>
```

Then open `http://127.0.0.1:8501` locally. Streamlit calls FastAPI locally on GB10, so neither port 8000 nor 8080 needs to be exposed to your laptop.

## Four-screen presentation flow

### 1. LIVE NVIDIA

Use ODI `11466150` and click **Run fresh NVIDIA inference**.

Show:

- real NHTSA narrative;
- local NVIDIA NIM model;
- structured failure signature;
- NVIDIA embedding model;
- vector dimension and latency;
- `cache_write = NO`;
- AI-vs-deterministic decision boundary.

### 2. TIME MACHINE

Use the audited Mach-E reference replay.

Show:

- historical cutoff slider;
- risk factors and evidence;
- original ODI complaint;
- recall outcome locked;
- **Reveal historical outcome** only after the detector snapshot is established;
- 15-day historical lead for the audited Mach-E example.

The optional live quick replay is available, but the audited reference artifact is the guaranteed presentation path.

### 3. ACTION CENTER

This is the business-facing improvement.

Generate the **Engineering Investigation Packet** from the selected Time Machine signal. Explain:

- no LLM generates the risk or recommendation;
- the packet reuses the persisted deterministic signal;
- top risk contributors are shown;
- supporting ODI records are included;
- the brief explicitly states that RecallZero ranks evidence and does not declare a defect.

Then show one of these:

1. **Always available:** download the Markdown brief and JSON evidence packet.
2. **Optional:** send the same grounded summary to a configured Slack/Teams/generic webhook.
3. **Optional:** run the NVIDIA Investigator Agent, which uses NeMo Agent Toolkit to orchestrate the five RecallZero tools.

There is also an optional **Investigate another vehicle live** expander using the real `/api/v1/analyze` path. Keep `refresh=false` and limit the complaint count for presentation reliability.

### 4. VALIDATION

Finish by showing the frozen preregistered result:

- target-qualified sensitivity: `1/10 = 10%`;
- positive vehicles with any pre-recall alert: `6/10` — explicitly **not** recall sensitivity;
- valid controls with unconfirmed alert: `1/9`;
- anti-leakage checks: pass;
- freeze/lock: pass;
- Detector v1 acceptance: **FAIL**.

This prevents the Mach-E success story from being presented as generalized performance.

## Optional agent architecture to explain to judges

```text
NVIDIA Investigator Agent (NeMo Agent Toolkit)
                 |
      +----------+-----------+-----------+-------------+
      |          |           |           |             |
    Fetch      Analyze    Time Machine  Evidence   Engineering Brief
      |          |           |           |             |
      +----------+-----------+-----------+-------------+
                 |
        RecallZero deterministic engine
```

Say: **"The agent orchestrates the investigation. It does not invent the evidence or calculate the safety score."**

## Demo-day fallback policy

- Fresh NIM trace fails → move to Time Machine; do not claim the failed call was live.
- Live quick replay fails → use the audited reference replay.
- Alert webhook fails → download the engineering packet; do not debug the integration on stage.
- NAT agent fails → show the tool map and continue; the agent is intentionally optional.
- Never clear the semantic cache or refresh NHTSA data immediately before presenting.
- Never change detector thresholds, weights, taxonomy, validation artifacts, or the reference replay for the demo.
