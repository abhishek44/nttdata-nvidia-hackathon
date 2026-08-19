# RecallZero 0.3.5.post5 Live Demo Build Validation

This is a presentation/runtime build layered on top of the frozen `0.3.5.post5` research baseline. It does **not** create a new detector version.

Added presentation/runtime surfaces:

- `GET /api/v1/demo/readiness`
- `POST /api/v1/demo/nvidia-trace`
- `demo/app.py` guided Streamlit UI
- audited Mach-E reference backtest asset
- frozen Detector v1 validation summary asset
- demo start/preflight scripts

The fresh NVIDIA trace reads one complaint from the normalized cache, calls the strict NVIDIA NIM extractor and NVIDIA embedder directly, and performs no signature-cache write or risk calculation.

Validation performed during packaging:

- `113 passed, 1 skipped` (`nat` runtime optional and not installed in packaging environment)
- `compileall` PASS for `src`, `demo`, and `scripts/demo_preflight.py`
- Detector v1 freeze verification PASS (`53` checks)
- Frozen Mach-E reference artifact: qualified alert `2022-05-26`, official recall `2022-06-10`, lead `15` days, all anti-leakage checks true

Detector/evaluation files named in `benchmarks/detector_freeze_v1.yaml` were not modified.
