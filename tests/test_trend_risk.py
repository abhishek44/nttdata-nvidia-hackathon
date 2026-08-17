from datetime import date, timedelta

from recallzero.analytics import RiskEngine, SeverityEngine, TrendEngine
from recallzero.config import RiskConfig
from recallzero.intelligence.extractor import HeuristicFailureExtractor
from recallzero.models import Complaint, RecallMatch, Vehicle


def _complaint(index: int, received: date) -> Complaint:
    return Complaint(
        odi_number=str(index),
        vehicle=Vehicle(make="Demo", model="EV", model_years=(2022,)),
        received_date=received,
        components=("SERVICE BRAKES",),
        narrative="Brake pedal became hard while driving and stopping distance increased.",
    )


def test_recent_spike_produces_explainable_risk() -> None:
    cutoff = date(2022, 6, 30)
    complaints = [_complaint(1, cutoff - timedelta(days=80))]
    complaints += [_complaint(index + 2, cutoff - timedelta(days=index * 3)) for index in range(8)]
    trend = TrendEngine().calculate(complaints, cutoff)

    import asyncio

    extractor = HeuristicFailureExtractor()
    async def collect():
        return await asyncio.gather(*(extractor.extract(item) for item in complaints))

    signatures = asyncio.run(collect())
    severity = SeverityEngine().calculate(complaints, signatures)
    risk = RiskEngine(RiskConfig(alert_threshold=50, minimum_evidence=4)).calculate(
        severity=severity,
        trend=trend,
        recall_match=RecallMatch(matched=False, score=0, reason="No recall matched."),
        evidence_count=len(complaints),
    )
    assert trend.recent_count >= 8
    assert trend.trend_ratio > 2
    assert risk.final_score >= 50
    assert risk.alert is True
    assert abs(sum(item.contribution for item in risk.factors) - risk.final_score) < 0.11


def test_severity_does_not_double_count_source_flags() -> None:
    complaint = _complaint(99, date(2022, 6, 1)).model_copy(update={"crash": True})
    import asyncio

    signature = asyncio.run(HeuristicFailureExtractor().extract(complaint))
    result = SeverityEngine().calculate([complaint], [signature])
    assert result.indicator_counts["crash_reported"] == 1
