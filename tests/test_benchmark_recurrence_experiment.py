from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from recallzero.benchmark import (
    BenchmarkAcceptanceCriteria,
    BenchmarkCandidateRecord,
    BenchmarkCaseResult,
    BenchmarkRunResult,
    BenchmarkSnapshotRecord,
)
from recallzero.benchmark_recurrence_experiment import build_recurrence_separation_experiment
from recallzero.models import Vehicle
from recallzero.utils import dumps_json


def _candidate(
    *,
    lineage_id: str,
    issue: str,
    evidence: int,
    risk: float,
    target: float | None,
    alert: bool = False,
) -> BenchmarkCandidateRecord:
    return BenchmarkCandidateRecord(
        signal_id=f"sig-{lineage_id}-{evidence}",
        lineage_id=lineage_id,
        signal_scope="cluster",
        issue=issue,
        failure_mechanism="OTHER",
        consequence_family="OTHER",
        evidence_count=evidence,
        risk_score=risk,
        risk_factors={},
        alert=alert,
        distance_to_alert_threshold=max(0.0, 75.0 - risk),
        threshold_margin=round(risk - 75.0, 2),
        posthoc_target_score=target,
        posthoc_target_breakdown={"final": target} if target is not None else {},
    )


def _write_experiment_raw(path: Path) -> None:
    vehicle = Vehicle(make="DEMO", model="CAR", model_years=(2020,))
    positive_snapshots = (
        BenchmarkSnapshotRecord(
            cutoff_date=date(2020, 1, 1),
            complaint_count_visible=5,
            signal_count=1,
            max_risk_score=60.0,
            top_candidates=(
                _candidate(
                    lineage_id="lin-target",
                    issue="Target slow burn",
                    evidence=2,
                    risk=60.0,
                    target=0.72,
                ),
            ),
        ),
        BenchmarkSnapshotRecord(
            cutoff_date=date(2020, 2, 5),
            complaint_count_visible=8,
            signal_count=1,
            max_risk_score=61.0,
            top_candidates=(
                _candidate(
                    lineage_id="lin-target",
                    issue="Target slow burn",
                    evidence=3,
                    risk=61.0,
                    target=0.70,
                ),
            ),
        ),
        BenchmarkSnapshotRecord(
            cutoff_date=date(2020, 4, 15),
            complaint_count_visible=12,
            signal_count=1,
            max_risk_score=66.0,
            top_candidates=(
                _candidate(
                    lineage_id="lin-target",
                    issue="Target slow burn",
                    evidence=5,
                    risk=66.0,
                    target=0.69,
                ),
            ),
        ),
        # Same evidence repeated later must not create a new recurrence event.
        BenchmarkSnapshotRecord(
            cutoff_date=date(2020, 5, 15),
            complaint_count_visible=12,
            signal_count=1,
            max_risk_score=66.0,
            top_candidates=(
                _candidate(
                    lineage_id="lin-target",
                    issue="Target slow burn",
                    evidence=5,
                    risk=66.0,
                    target=0.69,
                ),
            ),
        ),
    )
    positive = BenchmarkCaseResult(
        case_id="case-positive",
        name="Positive slow burn",
        expected_role="positive",
        benchmark_split="validation",
        vehicle=vehicle,
        campaign_number="20V000001",
        boundary_date=date(2020, 6, 1),
        status="NO_EARLY_SIGNAL",
        snapshots=positive_snapshots,
    )

    control_snapshots = (
        BenchmarkSnapshotRecord(
            cutoff_date=date(2020, 3, 1),
            complaint_count_visible=10,
            signal_count=1,
            max_risk_score=74.0,
            top_candidates=(
                _candidate(
                    lineage_id="lin-control",
                    issue="Control burst",
                    evidence=6,
                    risk=74.0,
                    target=None,
                ),
            ),
        ),
        BenchmarkSnapshotRecord(
            cutoff_date=date(2020, 3, 8),
            complaint_count_visible=12,
            signal_count=1,
            max_risk_score=74.5,
            top_candidates=(
                _candidate(
                    lineage_id="lin-control",
                    issue="Control burst",
                    evidence=8,
                    risk=74.5,
                    target=None,
                ),
            ),
        ),
        BenchmarkSnapshotRecord(
            cutoff_date=date(2020, 6, 1),
            complaint_count_visible=12,
            signal_count=1,
            max_risk_score=74.5,
            top_candidates=(
                _candidate(
                    lineage_id="lin-control",
                    issue="Control burst",
                    evidence=8,
                    risk=74.5,
                    target=None,
                ),
            ),
        ),
    )
    control = BenchmarkCaseResult(
        case_id="case-control",
        name="Near-threshold control",
        expected_role="negative",
        benchmark_split="validation",
        vehicle=Vehicle(make="DEMO", model="CONTROL", model_years=(2020,)),
        boundary_date=date(2021, 1, 1),
        status="CONTROL_QUIET",
        snapshots=control_snapshots,
    )

    raw = BenchmarkRunResult(
        benchmark_run_id="bench-recurrence-test",
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


def test_recurrence_experiment_counts_only_new_evidence_growth(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.json"
    _write_experiment_raw(raw_path)
    result = build_recurrence_separation_experiment(raw_result_path=raw_path)

    positive_case = next(row for row in result.cases if row.expected_role == "positive")
    positive = positive_case.selected_lineages[0]
    assert positive.evidence_growth_event_count == 3
    assert positive.evidence_growth_month_count == 3
    assert positive.evidence_growth_span_days == 105
    assert positive.evidence_gain_after_first == 3
    assert positive.recurrence_gates[0].passed is True

    control_case = next(row for row in result.cases if row.expected_role == "negative")
    control = control_case.selected_lineages[0]
    assert control.evidence_growth_event_count == 2
    assert control.evidence_growth_month_count == 1
    assert control.evidence_growth_span_days == 7
    assert control.recurrence_gates[0].passed is False


def test_recurrence_experiment_reports_case_level_separation(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.json"
    _write_experiment_raw(raw_path)
    result = build_recurrence_separation_experiment(raw_result_path=raw_path)

    primary = result.gate_separation[0]
    assert primary.positive_pass_count == 1
    assert primary.target_matched_positive_pass_count == 1
    assert primary.control_pass_count == 0
    assert primary.target_matched_positive_minus_control_rate == 1.0

    months = next(row for row in result.metric_separation if row.metric == "evidence_growth_month_count")
    assert months.target_matched_positive_vs_control_pairwise_auc == 1.0
    assert result.detector_recomputed is False
    assert result.risk_recomputed is False
    assert result.llm_calls_required == 0
    assert result.embedding_calls_required == 0


def test_recurrence_experiment_rejects_unverified_source(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.json"
    _write_experiment_raw(raw_path)
    raw_path.write_text(
        raw_path.read_text(encoding="utf-8").replace(
            '"freeze_verified": true', '"freeze_verified": false'
        ),
        encoding="utf-8",
    )
    try:
        build_recurrence_separation_experiment(raw_result_path=raw_path)
    except RuntimeError as exc:
        assert "freeze-verified" in str(exc)
    else:
        raise AssertionError("Expected unverified source benchmark to be rejected")
