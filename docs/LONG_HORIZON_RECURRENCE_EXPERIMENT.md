# Long-Horizon Recurrence Experiment A

This is a development-only screening experiment over an already completed, freeze-verified and lock-verified raw benchmark artifact. It does **not** modify or rerun Detector v1.

The question is narrow: do target-positive lineages show slow, repeated accumulation of independent evidence over months more often than the highest-risk lineages in valid negative controls?

The experiment deliberately does not count a lineage merely because it remains in the weekly top-N. A recurrence event is counted only when the lineage's running maximum `evidence_count` increases. This prevents weekly persistence of the same complaint records from masquerading as new recurrence.

Positive cases use the best persisted post-hoc target lineage. That selection uses future recall information and is therefore evaluation-only. Control cases use the highest frozen max-risk lineage and do not use target recall information.

Three fixed screening gates are reported without tuning Detector v1:

- `LHREC_PRIMARY_90D_3M_4E`: new evidence in at least 3 calendar months, spanning at least 90 days, maximum evidence at least 4, and at least 2 additional evidence records after first persistence.
- `LHREC_MODERATE_120D_4M_5E`: at least 4 growth months, 120 days, maximum evidence 5, gain after first 3.
- `LHREC_STRICT_180D_5M_6E`: at least 5 growth months, 180 days, maximum evidence 6, gain after first 4.

The gates are screening definitions, not proposed production thresholds. The output also reports continuous medians and pairwise AUC values for recurrence months, recurrence span, growth events, evidence gain, and maximum evidence so the hypothesis can be judged without fitting a threshold to the exposed cohort.

Run:

```bash
recallzero benchmark-recurrence-separation \
  data_detector_v1_localnim_post2/runs/detector_v1_validation_raw.json \
  --target-match-threshold 0.45 \
  --control-lineages-per-case 1 \
  --json data/runs/long_horizon_recurrence_experiment_a.json
```

Interpretation should focus on **separation**, especially between target-matched positive lineages and the highest-risk control lineages. Do not change the 75-point alert threshold from this experiment. If slow recurrence does not separate from controls, abandon this hypothesis rather than forcing Detector v2 to fit the exposed cohort.
