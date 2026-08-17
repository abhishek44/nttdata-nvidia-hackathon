# RecallZero Architecture

## Objective

RecallZero is an evidence-first engineering triage system for detecting potentially emerging vehicle failure patterns. Its core question is:

> Do multiple independent owner complaints describe a common safety-relevant failure pattern that is growing or persistent and is not already explained by a recall visible at the evidence cutoff?

A signal is a reason to investigate. It is not proof of a defect, root cause, or recall necessity.

## Processing layers

### 1. Evidence acquisition

`NHTSAClient` retrieves complaints and recalls for an exact make, model, and one or more model years. `FileRepository` stores:

- Raw API responses.
- Normalized typed records.
- Cached failure signatures.
- Analysis and backtest results.

Each normalized complaint preserves the NHTSA ODI identifier and raw source payload. This enables every cluster, signal, and brief to be traced back to the original evidence record.

### 2. Typed normalization

Pydantic contracts separate source evidence from interpretation:

```text
Complaint                         FailureSignature
---------                         ----------------
ODI identifier                    complaint_id link
received/incident date            system/subsystem
NHTSA component                   normalized failure mode
owner narrative                   symptom/operating state
crash/fire/injury flags           supported safety indicators
raw source payload                extraction method/confidence
```

The original complaint is not mutated by GenAI output.

### 3. Language understanding

`HybridFailureExtractor` uses:

1. NVIDIA NIM LLM structured extraction when configured.
2. A deterministic heuristic extractor when NIM is unavailable or returns invalid output.

The extraction prompt explicitly forbids the model from inventing trend, risk, recall status, causation, or complaint counts.

### 4. Semantic representation and grouping

`HybridEmbedder` uses NVIDIA embeddings when available and TF-IDF otherwise. `ComplaintClusterer` runs DBSCAN using cosine distance. The cluster document weights the normalized failure signature more heavily than the raw narrative to reduce fragmentation caused by narrative length and incidental details.

DBSCAN noise points are retained as singleton/noise clusters for transparency, but the evidence gate prevents them from becoming alerts under normal defaults.

### 5. Deterministic analytics

For each cluster at a cutoff date:

- Recent window: 28 days.
- Baseline window: preceding 84 days.
- Trend ratio: add-one-smoothed recent vs. baseline 28-day-equivalent rate.
- Persistence: consecutive recent weeks containing evidence.
- Evidence score: saturating function of complaint count.
- Severity: transparent supported-indicator scoring.
- Recall gap: inverse of the best visible recall-match similarity.

Default risk formula:

```text
Final risk = 0.30 severity
           + 0.25 trend
           + 0.15 persistence
           + 0.20 evidence
           + 0.10 recall gap
```

The default alert gate is `risk >= 75` with at least four supporting complaints. All values are configurable and should be calibrated.

### 6. Recall cross-reference

At any analysis cutoff, only recalls with a usable `report_received_date <= cutoff` are eligible. Text similarity combines cluster failure signatures with recall component, summary, consequence, and remedy. A match is suggestive, not a scope determination; engineers must verify campaign applicability.

### 7. Recall Time Machine

The Time Machine:

1. Excludes every complaint on or after the official recall date.
2. Replays weekly cutoffs strictly before that date.
3. Runs extraction, clustering, trend, risk, and visible-recall matching without target recall text.
4. Freezes each snapshot.
5. Introduces target recall text only for post-hoc matching of already-frozen alerts.
6. Records the first matching alert and lead time, or `NO_EARLY_SIGNAL`.

See `BACKTEST_PROTOCOL.md` for experimental controls.

### 8. Evidence critic and presentation

`EvidenceCritic` checks that:

- Cluster counts and evidence IDs agree.
- Every cluster member has evidence.
- Recent counts do not exceed total evidence.
- Risk contributions sum to the final score.
- Recall claims have a campaign identifier when marked matched.

It also adds causation caveats for crash/fire associations and warns on DBSCAN noise clusters.

### 9. Interfaces

- CLI: reproducible data, analysis, and backtest commands.
- FastAPI: typed service endpoints.
- Dashboard: lightweight Safety Radar.
- NeMo Agent Toolkit / AIQ: five registered tools that call the same deterministic pipeline.

The agent does not replace the analytical engine. It selects tools, asks for evidence, and explains returned results.

## Dependency direction

```text
models <- data/intelligence/analytics/recall
              \       |       /
                pipeline
              /    |      \
          backtest api     aiq
                    |
                    web
```

Core analytics do not import FastAPI or NeMo Agent Toolkit. NAT/AIQ is an optional integration layer, so the detector remains testable without an agent runtime.

## Extension points

Future additions can implement existing protocols/contracts:

- OEM warranty or dealer-repair adapters.
- Alternative extractors and embedders.
- HDBSCAN or supervised cluster assignment.
- Exposure denominators and complaint-rate normalization.
- Manufacturer communication and investigation matching.
- Calibrated probabilistic risk models.
- Batch scheduler and model-line surveillance.
