from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from recallzero.benchmark import aggregate_benchmark, load_candidates
from recallzero.config import Settings
from recallzero.freeze import verify_freeze


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_candidates_manifest_reuses_existing_shape_with_benchmark_extensions(tmp_path) -> None:
    path = tmp_path / "candidates.yml"
    path.write_text(
        """
        candidates:
          - name: Positive
            make: FORD
            model: MUSTANG MACH-E
            model_years: [2021, 2022]
            campaign_number: 22V412000
            official_recall_date: 2022-06-10
            expected_role: positive
            benchmark_split: development
          - name: Negative
            make: EXAMPLE
            model: CONTROL
            model_years: [2022]
            campaign_number: null
            evaluation_end_date: 2023-01-01
            adjudication_end_date: 2024-01-01
            expected_role: negative
            benchmark_split: holdout
        """,
        encoding="utf-8",
    )
    cases = load_candidates(path)
    assert len(cases) == 2
    assert cases[0].campaign_number == "22V412000"
    assert cases[0].boundary_date.isoformat() == "2022-06-10"
    assert cases[1].expected_role == "negative"
    assert cases[1].boundary_date.isoformat() == "2023-01-01"


def test_freeze_verification_checks_live_risk_config_and_module_hash(tmp_path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    module = project_root / "src/recallzero/analytics/severity.py"
    risk_file = tmp_path / "risk.yml"
    risk_file.write_text(
        """
        risk:
          weights:
            severity: 0.30
            trend: 0.25
            persistence: 0.15
            evidence: 0.20
            recall_gap: 0.10
          alert_threshold: 75.0
          minimum_evidence: 4
          medium_threshold: 45.0
          high_threshold: 70.0
          critical_threshold: 88.0
        """,
        encoding="utf-8",
    )
    settings = Settings(data_dir=tmp_path / "data", risk_file=risk_file, use_nim=False)
    manifest = {
        "freeze_id": "test-freeze",
        "detector_modules": {
            "severity": {"path": "src/recallzero/analytics/severity.py", "sha256": _sha(module)}
        },
        "runtime": {
            "models": {
                "llm_model": settings.llm_model,
                "embedding_model": settings.embedding_model,
                "use_nim": False,
            },
            "risk": settings.risk_config().model_dump(mode="python"),
            "clustering": settings.clustering.model_dump(mode="python"),
            "trend": settings.trend.model_dump(mode="python"),
        },
        "runtime_config_files": {
            "risk_file": {"path": str(risk_file), "sha256": _sha(risk_file)}
        },
    }
    path = tmp_path / "freeze.yml"
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    verified = verify_freeze(path, settings, project_root=project_root)
    assert verified.ok is True

    risk_file.write_text(risk_file.read_text().replace("75.0", "74.0", 1), encoding="utf-8")
    mismatch = verify_freeze(path, settings, project_root=project_root)
    assert mismatch.ok is False
    names = {item.name for item in mismatch.failures}
    assert "runtime.risk.alert_threshold" in names
    assert "runtime_config_files.risk_file.sha256" in names


def test_aggregate_benchmark_reports_lineage_and_snapshot_false_alert_burden() -> None:
    # Keep this test at the aggregate contract level; detector behavior is exercised elsewhere.
    from recallzero.benchmark import BenchmarkCaseResult
    from recallzero.models import Vehicle

    vehicle = Vehicle(make="EXAMPLE", model="CONTROL", model_years=(2022,))
    negative = BenchmarkCaseResult(
        case_id="case-1",
        name="control",
        expected_role="negative",
        benchmark_split="development",
        vehicle=vehicle,
        boundary_date="2023-01-01",
        status="CONTROL_ALERT_PRESENT",
        first_any_alert_date="2022-12-01",
        alert_snapshot_count=3,
        unique_alert_lineages=2,
        replay_years=1.0,
    )
    aggregate = aggregate_benchmark([negative])
    assert aggregate["raw_control_alert_snapshots_per_vehicle_replay_year"] == 3.0
    assert aggregate["raw_control_alert_lineages_per_vehicle_replay_year"] == 2.0
