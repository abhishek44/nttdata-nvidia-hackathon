# RecallZero v2 Release Validation

Release version: **0.3.5.post5** — read-only Long-Horizon Recurrence Separation Experiment.

## Scope

0.3.5.post5 does not tune Detector v1. It adds one offline development experiment over an already completed raw benchmark artifact. The historical Detector v1 Validation Run 1 remains the 0.3.5.post2 failed-but-informative result and must not be rewritten by this experiment.

The command is:

```bash
recallzero benchmark-recurrence-separation RAW_RESULT \
  --target-match-threshold 0.45 \
  --control-lineages-per-case 1 \
  --json long_horizon_recurrence_experiment_a.json
```

## Experimental boundary

The recurrence experiment:

- requires the source raw benchmark to report both `freeze_verified=true` and `lock_verified=true`;
- performs zero LLM calls;
- performs zero embedding calls;
- performs zero detector replays;
- performs zero clustering or risk recomputation;
- uses the best persisted post-hoc target lineage per valid positive case only for offline development analysis;
- uses the highest frozen max-risk lineage per valid negative control as the false-alert stress comparator;
- never feeds post-hoc target information into live detector scoring;
- counts recurrence only when the running maximum `evidence_count` increases, so repeated weekly persistence without new independent evidence does not inflate recurrence;
- reports three fixed screening gates and continuous pairwise separation statistics rather than fitting a Detector v2 threshold to the exposed cohort.

The raw benchmark persists ordinary top-N candidates by risk plus every alert, not every detector signal. A lineage may also first enter the persisted top-N after several complaints already exist, so observed evidence-growth months are conservative and can undercount earlier recurrence.

## Freeze provenance

- Detector source logic is byte-identical to 0.3.5.post4.
- `src/recallzero/cli.py` changed only to expose the new read-only experiment command.
- `src/recallzero/benchmark_recurrence_experiment.py` is new diagnostic-only code.
- `benchmarks/detector_freeze_v1.yaml` records the post5 diagnostic CLI/module hashes.
- Exact post2/post3/post4 freeze manifests are preserved under `benchmarks/archive/` for historical provenance.

## Build validation

- `PYTHONPATH=src pytest -ra`: **111 collected, 110 passed, 1 skipped, 0 failed**.
- The one skip is the optional NAT runtime integration because `nat` is not installed in the isolated build environment.
- `python -m compileall -q src tests`: **PASS**.
- `PYTHONPATH=src python -m recallzero.cli --version`: **RecallZero 0.3.5.post5**.
- `freeze-verify --manifest benchmarks/detector_freeze_v1.yaml`: **PASS**.
- `benchmark-recurrence-separation --help`: **PASS**.
- Synthetic recurrence separation regression smoke: **PASS**.
- Wheel build with local build environment: **PASS**.
- Clean source ZIP pytest/compileall/freeze verification: **PASS**.
- Isolated `--target` wheel import reports **0.3.5.post5** and imports `build_recurrence_separation_experiment`: **PASS**.
- Wheel SHA-256: `afcd906a10f2007c50ee523609588f87c26766409f107d82689d2d7c1fda5b1d`.
- Current post5 freeze manifest SHA-256: `29ef94ffba869dc5fc8eefa3c96859a889f5fa186497f6de69e73420e50c74ee`.
- Archived post4 freeze SHA-256: `b6348e5b59b96d3e9404f075ff8c33bddeb34483a74c03dd7215a1ef4198895b`.
