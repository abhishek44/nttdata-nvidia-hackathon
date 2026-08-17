# NeMo Agent Toolkit and AI-Q Integration

## Terminology

- **NeMo Agent Toolkit (NAT):** current general-purpose NVIDIA agent framework and `nat` CLI.
- **AI-Q Blueprint:** NVIDIA deep-research blueprint built on NAT.
- **Legacy AIQ Toolkit:** earlier package/CLI using the `aiq` namespace.

RecallZero includes modern NAT registration, a legacy compatibility registration, a standalone native tool-calling workflow, and a stock AI-Q Blueprint YAML patcher. The modern registration prefers the NAT 1.8+ `nat.plugin_api` facade and falls back to the documented NAT 1.5-1.7 import locations. The deprecated `aiq` module is not required for current AI-Q Blueprint releases.

## Registered tools

| Tool | Purpose | Numerical authority |
|---|---|---|
| `recallzero_fetch_vehicle_data` | Fetch/cache NHTSA complaints and recalls | Source counts only |
| `recallzero_analyze_vehicle` | Full clustering, trend, severity, risk, and recall-gap analysis | Deterministic pipeline |
| `recallzero_run_backtest` | Leakage-safe weekly historical replay | Deterministic Time Machine |
| `recallzero_get_evidence` | Retrieve an original cached complaint by ODI ID | Source record |
| `recallzero_engineering_brief` | Render a saved signal with critic checks | Uses stored calculation |

Each tool accepts one typed Pydantic input object. Tool output is compact JSON except the engineering brief, which returns Markdown.

## Plugin discovery

`pyproject.toml` registers:

```toml
[project.entry-points.'nat.plugins']
recallzero = "recallzero.aiq.register"

[project.entry-points.'aiq.components']
recallzero = "recallzero.aiq.legacy_register"
```

Install RecallZero in the same Python environment as NAT/AI-Q so the runtime can discover the entry point.

## Standalone NAT workflow

Install and run:

```bash
python -m pip install -e ".[aiq]"
export NVIDIA_API_KEY=nvapi-...
export RECALLZERO_DATA_DIR="$PWD/data"

nat run \
  --config_file configs/aiq/recallzero_agent.yml \
  --input "Investigate the 2021 and 2022 Ford Mustang Mach-E."
```

The NAT 1.8 workflow uses `tool_calling_agent` with a NIM LLM. The YAML contains only fields documented for the NAT 1.8 tool-calling agent; the unsupported `thinking` key is intentionally absent. Grounding rules and metric definitions are also returned inside the `recallzero_analyze_vehicle` tool payload so the final response preserves deterministic semantics.

## Local NIM with NAT

```bash
export RECALLZERO_NIM_BASE_URL=http://127.0.0.1:8000/v1
export RECALLZERO_EMBEDDING_BASE_URL=http://127.0.0.1:8001/v1
export RECALLZERO_AGENT_MODEL=<local-chat-model>
export RECALLZERO_LLM_MODEL=<local-chat-model>
export RECALLZERO_EMBEDDING_MODEL=<local-embedding-model>
```

The standalone YAML uses environment interpolation for model/base URL. The RecallZero tools independently read the same environment variables through `Settings`.

## Integrating with a stock AI-Q Blueprint

Activate the environment that already runs the stock Blueprint:

```bash
source /path/to/aiq-blueprint/.venv/bin/activate
cd /path/to/recallzero_v2

./scripts/integrate_stock_aiq.sh \
  /path/to/aiq-blueprint/configs/config_cli_default.yml \
  /path/to/aiq-blueprint/configs/config_cli_recallzero.yml
```

The script performs:

```bash
python -m pip install -e /path/to/recallzero_v2
recallzero-aiq-patch SOURCE --output DESTINATION
```

The patcher:

1. Adds all RecallZero function definitions under top-level `functions`.
2. Detects the current AI-Q `data_source_registry` and adds a default-enabled **RecallZero Vehicle Safety** source. Agents whose `tools` field is omitted continue inheriting all registered sources, so existing web/paper tools are not accidentally hidden.
3. Extends explicit shallow/deep researcher tool lists used by older or customized configurations. Explicit intent/clarifier tool lists are also extended; omitted lists are preserved.
4. Supports standalone ReAct/tool-calling workflows that use `tool_names`.
5. Is idempotent and leaves the original config untouched.

Review the generated diff before launching:

```bash
diff -u stock.yml stock_with_recallzero.yml
```

If your AI-Q Blueprint uses a custom configuration structure, register the functions with the patcher and manually add these names to the chosen agent's `tools` list:

```yaml
- recallzero_analyze_vehicle
- recallzero_run_backtest
- recallzero_get_evidence
- recallzero_engineering_brief
```

## Suggested stock AI-Q query

```text
Investigate the 2021 and 2022 Ford Mustang Mach-E using RecallZero. First run the
vehicle analysis with a cutoff of 2022-06-09. Then run the leakage-safe historical
backtest for NHTSA campaign 22V412000 with official recall date 2022-06-10. Preserve
NO_EARLY_SIGNAL if that is the result, report all anti-leakage checks, and cite ODI IDs
for any supporting evidence.
```

## Agent grounding contract

The analysis tool returns explicit `agent_grounding_rules` and `metric_definitions`. They require the agent to:

- Use returned deterministic numerical fields rather than estimating them.
- Preserve the configured recent/baseline windows and never rename `trend_ratio` as week-over-week unless that is literally the configured window.
- Distinguish an engineering-prioritization signal from proof of a defect.
- Avoid causal claims from crash/fire/injury association flags.
- Treat low recall similarity as insufficient evidence of a recall-scope gap.
- Prefer representative ODI identifiers for evidence drill-down rather than flooding the context with all complaint IDs.
- Treat `DEGRADED` semantic/clustering quality as provisional.
- Preserve `NO_EARLY_SIGNAL`/`INVALID_BACKTEST` and anti-leakage checks for historical results.

## Compatibility notes

- `configs/aiq/recallzero_agent.yml`: current NAT (`nat run`).
- `configs/aiq/legacy_recallzero_agent.yml`: optional legacy AIQ (`aiq run`); install only for an older environment that still uses the deprecated CLI.
- `configs/aiq/stock_aiq_additions.yml`: human-readable merge fragment.
- `recallzero-aiq-patch`: automated config patch utility.

NAT and AI-Q Blueprint schemas continue to evolve. Validate the patched config with the version installed in your stock environment before operational use.
