# Source-to-Implementation Traceability

RecallZero v2 was rebuilt from the supplied project materials rather than attempting to reconstruct the lost source line-for-line.

| Supplied design concept | Implementation |
|---|---|
| Public NHTSA complaints and recalls | `data/nhtsa_client.py`, typed normalization, immutable raw cache |
| Common failure signatures from varied complaint language | NIM structured extractor plus deterministic fallback |
| Two-level grouping: component and specific failure mode | `FailureSignature` plus DBSCAN semantic clusters |
| Deterministic recent-vs-baseline analytics | 28-day recent and 84-day baseline `TrendEngine` |
| Explainable weighted risk | Configurable severity/trend/persistence/evidence/recall-gap factors |
| Complaint spike with no matching visible recall | Cutoff-aware recall matcher and recall-gap factor |
| Evidence-grounded engineering brief | ODI-linked evidence package and deterministic evidence critic |
| Recall Time Machine | Weekly, pre-recall-only replay with post-hoc target matching |
| No future-recall leakage | Target campaign removed during detection; invalidation checks recorded |
| Agentic investigation | Five NeMo Agent Toolkit/AIQ tools and standalone ReAct workflow |
| Integration with stock AI-Q | Plugin entry points and non-destructive YAML patch utility |

## Deliberate trust boundary

The LLM extracts and normalizes meaning. It does not calculate complaint counts, dates, windows, trend values, persistence, numerical risk, recall lead time, or anti-leakage status. Those values are produced by deterministic code and remain traceable to complaint identifiers.
