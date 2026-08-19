from __future__ import annotations

from datetime import date, datetime, UTC
from pathlib import Path

from recallzero.benchmark import (
    BenchmarkAcceptanceCriteria,
    BenchmarkCandidateRecord,
    BenchmarkCaseResult,
    BenchmarkRunResult,
    BenchmarkSnapshotRecord,
)
from recallzero.benchmark_risk_audit import build_target_risk_audit
from recallzero.models import Vehicle
from recallzero.utils import dumps_json


def _candidate(
    *,
    signal_id: str,
    lineage_id: str,
    target_score: float,
    risk_score: float,
    alert: bool,
    severity: float,
    trend: float,
    evidence_count: int = 4,
) -> BenchmarkCandidateRecord:
    threshold = 75.0
    factors = {
        "severity": {
            "score": severity,
            "weight": 0.30,
            "contribution": round(severity * 0.30, 2),
            "explanation": "severity",
        },
        "trend": {
            "score": trend,
            "weight": 0.25,
            "contribution": round(trend * 0.25, 2),
            "explanation": "trend",
        },
        "persistence": {"score": 40.0, "weight": 0.15, "contribution": 6.0},
        "evidence": {"score": 40.0, "weight": 0.20, "contribution": 8.0},
        "recall_gap": {"score": 100.0, "weight": 0.10, "contribution": 10.0},
    }
    return BenchmarkCandidateRecord(
        signal_id=signal_id,
        lineage_id=lineage_id,
        signal_scope="cluster",
        issue="Target-ish signal",
        failure_mechanism="OTHER",
        consequence_family="OTHER",
        evidence_count=evidence_count,
        risk_score=risk_score,
        risk_factors=factors,
        alert=alert,
        distance_to_alert_threshold=max(0.0, threshold - risk_score),
        threshold_margin=round(risk_score - threshold, 2),
        posthoc_target_score=target_score,
        posthoc_target_breakdown={"component": 1.0, "final": target_score},
    )


def _write_raw(path: Path) -> None:
    vehicle = Vehicle(make="DEMO", model="CAR", model_years=(2020,))
    target_low_risk = _candidate(
        signal_id="sig-target-1",
        lineage_id="lin-target",
        target_score=0.60,
        risk_score=42.0,
        alert=False,
        severity=20.0,
        trend=30.0,
    )
    target_later = _candidate(
        signal_id="sig-target-2",
        lineage_id="lin-target",
        target_score=0.58,
        risk_score=60.0,
        alert=False,
        severity=50.0,
        trend=60.0,
    )
    off_target_alert = _candidate(
        signal_id="sig-alert",
        lineage_id="lin-alert",
        target_score=0.02,
        risk_score=82.0,
        alert=True,
        severity=90.0,
        trend=90.0,
        evidence_count=20,
    )
    positive = BenchmarkCaseResult(
        case_id="case-positive",
        name="Positive",
        expected_role="positive",
        benchmark_split="validation",
        vehicle=vehicle,
        campaign_number="20V000001",
        boundary_date=date(2020, 6, 1),
        status="EARLY_ALERT_TARGET_UNMATCHED",
        snapshots=(
            BenchmarkSnapshotRecord(
                cutoff_date=date(2020, 4, 1),
                complaint_count_visible=10,
                signal_count=2,
                max_risk_score=82.0,
                top_candidates=(target_low_risk, off_target_alert),
            ),
            BenchmarkSnapshotRecord(
                cutoff_date=date(2020, 5, 1),
                complaint_count_visible=20,
                signal_count=1,
                max_risk_score=60.0,
                top_candidates=(target_later,),
            ),
        ),
    )
    control = BenchmarkCaseResult(
        case_id="case-control",
        name="Control",
        expected_role="negative",
        benchmark_split="validation",
        vehicle=Vehicle(make="DEMO", model="CONTROL", model_years=(2020,)),
        boundary_date=date(2021, 1, 1),
        status="CONTROL_QUIET",
    )
    raw = BenchmarkRunResult(
        benchmark_run_id="bench-test",
        benchmark_id="detector-v1-test",
        benchmark_split="validation",
        freeze_id="recallzero-detector-v1",
        freeze_verified=True,
        lock_verified=True,
        manifest_path="config/candidates.yml",
        manifest_sha256="abc",
        lock_path="benchmarks/test.lock.json",
        freeze_manifest_path="benchmarks/detector_freeze_v1.yaml",
        case_count=2,
        cases=(positive, control),
        aggregate={},
        acceptance_criteria=BenchmarkAcceptanceCriteria(),
        created_at=datetime.now(UTC),
    )
    path.write_text(dumps_json(raw.model_dump(mode="json")), encoding="utf-8")


def test_target_risk_audit_separates_target_lineage_from_off_target_alert(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.json"
    _write_raw(raw_path)
    result = build_target_risk_audit(raw_result_path=raw_path)

    assert result.detector_recomputed is False
    assert result.llm_calls_required == 0
    assert result.embedding_calls_required == 0
    assert result.alert_threshold == 75.0
    assert result.positive_case_count == 1
    case = result.cases[0]
    assert case.best_persisted_target_lineage_id == "lin-target"
    assert case.best_persisted_target_score == 0.60
    assert case.best_persisted_target_lineage is not None
    assert case.best_persisted_target_lineage.max_risk_score == 60.0
    assert case.best_persisted_target_lineage.ever_alerted is False
    assert case.best_alerted_lineage is not None
    assert case.best_alerted_lineage.lineage_id == "lin-alert"
    assert case.diagnosis == "TARGET_MATCHED_BUT_RISK_GATE_NOT_MET"


def test_target_risk_audit_ranks_weighted_headroom(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.json"
    _write_raw(raw_path)
    result = build_target_risk_audit(raw_result_path=raw_path)
    best = result.cases[0].best_persisted_target_lineage
    assert best is not None
    factors = best.risk_factors_at_max_risk
    assert factors
    # At the max-risk occurrence, severity has 15 points of weighted headroom,
    # larger than trend's 10 and the remaining factors.
    assert factors[0].name == "severity"
    assert factors[0].weighted_headroom == 15.0
    assert factors[0].risk_if_factor_perfect == 75.0
    assert factors[0].alone_could_cross_threshold is True


def test_target_risk_audit_refuses_unverified_source(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.json"
    _write_raw(raw_path)
    data = raw_path.read_text(encoding="utf-8").replace('"freeze_verified": true', '"freeze_verified": false')
    raw_path.write_text(data, encoding="utf-8")
    try:
        build_target_risk_audit(raw_result_path=raw_path)
    except RuntimeError as exc:
        assert "freeze-verified" in str(exc)
    else:
        raise AssertionError("Expected unverified source benchmark to be rejected")
