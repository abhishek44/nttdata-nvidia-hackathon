from datetime import date

import pytest

from recallzero.backtest import RecallTimeMachine
from recallzero.config import Settings
from recallzero.demo import build_demo_records
from recallzero.pipeline import build_pipeline


@pytest.mark.asyncio
async def test_time_machine_excludes_future_data(tmp_path) -> None:
    vehicle, complaints, recalls, target, official_date = build_demo_records()
    # Add an obviously matching complaint after the recall; it must never be visible.
    future = complaints[0].model_copy(
        update={"odi_number": "FUTURE", "received_date": date(2022, 7, 1), "narrative": "Future loss of motive power report."}
    )
    complaints = [*complaints, future]
    settings = Settings(data_dir=tmp_path, use_nim=False)
    pipeline = build_pipeline(settings, use_nim=False)
    result = await RecallTimeMachine(pipeline, target_match_threshold=0.10).run(
        vehicle=vehicle,
        complaints=complaints,
        recalls=recalls,
        target_recall=target,
        official_recall_date=official_date,
        replay_start_date=date(2022, 3, 1),
        alert_threshold=50,
        minimum_evidence=4,
        save=False,
    )
    assert result.anti_leakage_checks["complaints_strictly_before_official_recall"] is True
    assert result.anti_leakage_checks["snapshots_strictly_before_official_recall"] is True
    assert result.anti_leakage_checks["snapshot_evidence_strictly_before_official_recall"] is True
    assert result.anti_leakage_checks["target_recall_not_used_during_detection"] is True
    assert result.latest_complaint_date_used < official_date
    assert all(
        evidence.complaint_id != "FUTURE"
        for snapshot in result.snapshots
        for signal in snapshot.alerts
        for evidence in signal.evidence
    )


@pytest.mark.asyncio
async def test_time_machine_invalidates_target_public_before_declared_boundary(tmp_path) -> None:
    vehicle, complaints, recalls, target, official_date = build_demo_records()
    target = target.model_copy(update={"report_received_date": date(2022, 5, 1)})
    recalls = [target]
    pipeline = build_pipeline(Settings(data_dir=tmp_path, use_nim=False), use_nim=False)
    result = await RecallTimeMachine(pipeline, target_match_threshold=0.10).run(
        vehicle=vehicle,
        complaints=complaints,
        recalls=recalls,
        target_recall=target,
        official_recall_date=official_date,
        replay_start_date=date(2022, 3, 1),
        alert_threshold=50,
        minimum_evidence=4,
        save=False,
    )
    assert result.status == "INVALID_BACKTEST"
    assert result.anti_leakage_checks["target_recall_not_used_during_detection"] is False
    assert result.lead_time_days is None
