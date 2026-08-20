from datetime import date, timedelta

from recallzero.analytics import TrendEngine
from recallzero.analytics.coverage import CoverageEngine
from recallzero.analytics.risk_engine_v2 import RiskEngineV2
from recallzero.analytics.risk_v2_config import DetectorV2Config, RiskConfigV2, RiskWeightsV2
from recallzero.analytics.severity import SeverityEngine
from recallzero.detector_v2 import _v2_to_v1_risk_config, load_detector_v2_config
from recallzero.intelligence.extractor import HeuristicFailureExtractor
from recallzero.models import Complaint, RecallMatch, Vehicle


def _complaint(index: int, received: date, year: int = 2022, vin: str | None = None, crash: bool = False) -> Complaint:
    return Complaint(
        odi_number=str(index),
        vehicle=Vehicle(make="Demo", model="EV", model_years=(year,)),
        received_date=received,
        components=("SERVICE BRAKES",),
        narrative="Brake pedal became hard while driving and stopping distance increased.",
        vin_prefix=vin,
        crash=crash,
    )


def test_v2_config_defaults():
    config = load_detector_v2_config()
    assert config.trend.recent_window_days == 30
    assert config.trend.baseline_window_days == 90
    assert config.risk.alert_threshold == 40.0
    w = config.risk.weights
    total = (
        w.volume_increase + w.vehicle_coverage + w.safety_consequence
        + w.persistence + w.model_year_breadth + w.no_matching_recall
    )
    assert abs(total - 1.0) < 1e-6


def test_coverage_split_subscores():
    engine = CoverageEngine()
    # 6 complaints across 3 model years, 4 distinct VIN prefixes, single vehicle.
    cutoff = date(2022, 6, 30)
    complaints = [
        _complaint(1, cutoff, year=2020, vin="1FA6P8TH0L1"),
        _complaint(2, cutoff, year=2021, vin="1FA6P8TH0L2"),
        _complaint(3, cutoff, year=2022, vin="1FA6P8TH0L3"),
        _complaint(4, cutoff, year=2022, vin="1FA6P8TH0L4"),
        _complaint(5, cutoff, year=2022, vin=None),
        _complaint(6, cutoff, year=2022, vin=None),
    ]
    result = engine.calculate(complaints, distinct_vehicles=1)
    assert result.distinct_model_years == 3
    assert result.model_year_span == 3
    assert result.distinct_vin_prefixes == 4
    assert result.distinct_vehicles == 1
    # model-year breadth and vehicle coverage are independent sub-scores
    assert 0.0 <= result.model_year_breadth_score <= 100.0
    assert 0.0 <= result.vehicle_coverage_score <= 100.0
    assert result.model_year_breadth_score > 0.0  # 3 distinct years
    # rescale crediting cross-vehicle recurrence should raise vehicle coverage
    rescaled = engine.rescale(
        evidence_count=result.distinct_vin_prefixes and 6 or 6,
        distinct_model_years=result.distinct_model_years,
        distinct_vin_prefixes=result.distinct_vin_prefixes,
        vin_evidence_ratio=result.vin_evidence_ratio,
        model_year_span=result.model_year_span,
        distinct_vehicles=3,
    )
    assert rescaled.vehicle_coverage_score > result.vehicle_coverage_score


def test_risk_engine_v2_six_factors():
    cutoff = date(2022, 6, 30)
    complaints = [_complaint(1, cutoff - timedelta(days=80), year=2021)]
    complaints += [
        _complaint(index + 2, cutoff - timedelta(days=index * 3), year=2022, crash=(index < 2))
        for index in range(8)
    ]
    trend = TrendEngine(recent_window_days=30, baseline_window_days=90).calculate(complaints, cutoff)
    import asyncio

    extractor = HeuristicFailureExtractor()

    async def collect():
        return await asyncio.gather(*(extractor.extract(item) for item in complaints))

    signatures = asyncio.run(collect())
    severity = SeverityEngine().calculate(complaints, signatures)
    coverage = CoverageEngine().calculate(complaints, distinct_vehicles=1)
    risk = RiskEngineV2(RiskConfigV2()).calculate(
        severity=severity,
        trend=trend,
        recall_match=RecallMatch(matched=False, score=0, reason="No recall matched."),
        coverage=coverage,
        evidence_count=len(complaints),
    )
    names = [factor.name for factor in risk.factors]
    assert names == [
        "volume_increase",
        "vehicle_coverage",
        "safety_consequence",
        "persistence",
        "model_year_breadth",
        "no_matching_recall",
    ]
    assert abs(sum(f.contribution for f in risk.factors) - risk.final_score) < 0.11
    # no matching recall found -> full no_matching_recall contribution
    no_recall = next(f for f in risk.factors if f.name == "no_matching_recall")
    assert no_recall.score == 100.0


def test_v2_weights_map_onto_v1_ordering_config():
    config = DetectorV2Config()
    v1 = _v2_to_v1_risk_config(config)
    w = v1.weights
    assert abs((w.severity + w.trend + w.persistence + w.evidence + w.recall_gap) - 1.0) < 1e-6
    assert v1.alert_threshold == 40.0


def test_risk_weights_v2_sum_validation():
    import pytest

    with pytest.raises(ValueError):
        RiskWeightsV2(volume_increase=0.9)  # sums to > 1.0