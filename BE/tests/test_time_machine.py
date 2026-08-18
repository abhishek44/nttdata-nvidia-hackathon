from datetime import date, timedelta

from recallzero.backtest.time_machine import run_time_machine
from recallzero.models import Complaint, EnrichedComplaint, FailureSignature, VehicleKey


def test_time_machine_never_uses_post_recall_data():
    vehicle = VehicleKey(make="Demo", model="EV", model_year=2022)
    start = date(2022, 1, 1)
    rows = []
    for i, offset in enumerate([0, 20, 40, 60, 70, 78, 85, 92, 99, 150]):
        c = Complaint(
            odi_number=i,
            vehicle=vehicle,
            date_complaint_filed=start + timedelta(days=offset),
            components=["POWER TRAIN"],
            summary="Vehicle lost motive power while driving.",
        )
        rows.append(EnrichedComplaint(
            complaint=c,
            signature=FailureSignature(
                system="POWER TRAIN",
                failure_mode="LOSS_OF_MOTIVE_POWER",
                operating_state="VEHICLE_MOVING",
                consequence="LOSS OF MOTIVE POWER",
                severity_indicators=["LOSS_OF_MOTIVE_POWER"],
                confidence=0.9,
            ),
            cluster_id="cluster-000",
        ))
    recall_date = date(2022, 5, 1)
    result = run_time_machine(rows, "DEMO", recall_date, alert_threshold=65, min_reports=4)
    assert all(date.fromisoformat(p["date"]) < recall_date for p in result.timeline)
    assert max(p["reports"] for p in result.timeline) <= 9  # the post-recall complaint is excluded
