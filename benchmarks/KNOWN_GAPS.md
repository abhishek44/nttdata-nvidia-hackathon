# Detector Freeze v1 — known deferred gaps

These items are deliberately **not fixed in 0.3.5/0.3.5.post1**. Detector v1 is frozen; the validation cohort should determine whether these are systemic generalization problems before Detector v2 changes are proposed.

## TAX-001 — `OTHER` consequence consistency bypass

`mechanism_is_consistent()` can allow a mechanism assignment to pass when `consequence_family == "OTHER"`. Because human-visible cluster labels are derived from raw NIM failure-mode text, a label can look semantically specific while the normalized consequence axis is `OTHER`, allowing contradictory mechanism assignments to survive.

**Benchmark interpretation:** cluster lineage depends on `(system, failure_mechanism, consequence_family)` and meta lineage on `failure_mechanism`. One physical pattern can therefore fragment into several lineage IDs. A spike in `unique_alert_lineages` or `unconfirmed_alert_lineages_per_vehicle_replay_year` must first be inspected for evidence overlap / lineage fragmentation before it is interpreted as many distinct false alarms.

## META-001 — mechanism meta-signal over-aggregation

Mechanism-level meta-signals can become broad in mature datasets. The known Mach-E current-state example showed that one high-voltage meta-signal could span many evidence records and consequence categories. This may be appropriate signal consolidation, or it may admit unrelated evidence because mechanism membership is too permissive.

**Benchmark interpretation:** inspect evidence composition, source systems, consequence distributions, risk-factor histories, and whether control alerts are driven by broad meta-signals before adding meta-membership confidence or a fusion graph.

## Evaluation labels are not ground truth of defect invalidity

A targetless control alert that does not match a recall inside the preregistered adjudication horizon is labeled `UNCONFIRMED_ALERT`. That is an operational burden metric, not proof that the safety concern was false. A control alert that later matches a recall is classified separately as `FUTURE_RECALL_ASSOCIATED` and is not counted as unconfirmed burden.
