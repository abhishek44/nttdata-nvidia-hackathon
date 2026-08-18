# Detector Freeze v1 — known deferred gaps

These items are deliberately **not fixed in 0.3.4**. The benchmark should determine whether they are systemic generalization problems or single-vehicle artifacts before Detector v2 changes are proposed.

## TAX-001 — `OTHER` consequence consistency bypass

`mechanism_is_consistent()` currently allows a mechanism assignment to pass when `consequence_family == "OTHER"`. Because human-visible cluster labels are derived from raw NIM failure-mode text, a label can look semantically specific while the normalized consequence axis is `OTHER`, allowing contradictory mechanism assignments to survive.

**Benchmark interpretation:** a spike in `unique_alert_lineages` must first be inspected for taxonomy-driven lineage fragmentation before it is interpreted as many distinct false alarms. Cluster lineage currently depends on `(system, failure_mechanism, consequence_family)` and meta lineage on `failure_mechanism`.

## META-001 — mechanism meta-signal over-aggregation

Mechanism-level meta-signals can become broad in mature datasets. The Mach-E current-state high-voltage signal contains many records and consequence categories. This may be appropriate signal consolidation, or it may admit unrelated evidence because mechanism membership is too permissive.

**Benchmark interpretation:** inspect evidence composition, source systems, consequence distributions, and factor histories across vehicles before adding meta-membership confidence or a fusion graph.
