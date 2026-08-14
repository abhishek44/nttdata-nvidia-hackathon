from datetime import date, timedelta

from recallzero.analytics.risk import score_risk
from recallzero.analytics.trends import trend_metrics
from recallzero.models import Complaint, EnrichedComplaint, FailureSignature, VehicleKey


def make_rows():
    vehicle = VehicleKey(make="Demo", model="EV", model_year=2022)
    start = date(2022, 1, 1)
    offsets = [0, 30, 50, 65, 72, 78, 83, 88, 92]
    rows = []
    for i, offset in enumerate(offsets):
        c = Complaint(
            odi_number=i,
            vehicle=vehicle,
            date_complaint_filed=start + timedelta(days=offset),
            components=["STEERING"],
            summary="Power steering failed while driving.",
        )
        rows.append(EnrichedComplaint(
            complaint=c,
            signature=FailureSignature(
                system="STEERING",
                subsystem="ELECTRIC_POWER_STEERING",
                failure_mode="LOSS_OF_STEERING",
                operating_state="VEHICLE_MOVING",
                consequence="REDUCED STEERING CONTROL",
                severity_indicators=["LOSS_OF_STEERING"],
                confidence=0.9,
            ),
            cluster_id="cluster-000",
        ))
    return rows


def test_risk_increases_for_accelerating_safety_cluster():
    rows = make_rows()
    cutoff = date(2022, 4, 10)
    trend = trend_metrics(rows, cutoff)
    risk = score_risk(rows, trend, recall_exists=False)
    assert trend.acceleration > 1
    assert risk.severity >= 70
    assert risk.final_score >= 60
