from __future__ import annotations

from datetime import date
from pathlib import Path

from recallzero.analytics import TrendEngine
from recallzero.analytics.coverage import CoverageEngine
from recallzero.analytics.risk_engine_v2 import RiskEngineV2
from recallzero.analytics.risk_v2_config import DetectorV2Config
from recallzero.config import RiskConfig, Settings, get_settings
from recallzero.models import AnalysisRun, Vehicle
from recallzero.models.v2 import CoverageMetrics, DetectorV2Result, SignalV2
from recallzero.pipeline import RecallZeroPipeline, build_pipeline

_DEFAULT_CONFIG = Path("config/detector_v2.yml")


def load_detector_v2_config(path: Path | None = None) -> DetectorV2Config:
    return DetectorV2Config.from_yaml(path or _DEFAULT_CONFIG)


def _v2_to_v1_risk_config(config: DetectorV2Config) -> RiskConfig:
    """Map the v2 weights onto the frozen v1 RiskConfig field names.

    This is used ONLY to drive a sensible signal ordering / alert gate inside the frozen
    pipeline. The authoritative Detector v2 score is recomputed separately by
    RiskEngineV2 with the full six-factor breakdown.
    """

    weights = config.risk.weights
    return RiskConfig(
        weights={
            "severity": weights.safety_consequence,
            "trend": weights.volume_increase,
            "persistence": weights.persistence,
            "evidence": weights.vehicle_coverage + weights.model_year_breadth,
            "recall_gap": weights.no_matching_recall,
        },
        alert_threshold=config.risk.alert_threshold,
        minimum_evidence=config.risk.minimum_evidence,
        medium_threshold=config.risk.medium_threshold,
        high_threshold=config.risk.high_threshold,
        critical_threshold=config.risk.critical_threshold,
    )


class DetectorV2:
    """Detector v2: composes the frozen Detector v1 evidence pipeline with new
    30/90 trend windows and the six-factor deterministic risk engine.

    No Detector v1 (frozen) file is modified. The v1 pipeline is instantiated with a
    v2-configured TrendEngine; its clustering, extraction, severity, and recall matching
    are reused unchanged so evidence lineage stays intact. The authoritative v2 risk is
    recomputed per signal by RiskEngineV2 with the CoverageEngine breadth factors.
    """

    def __init__(self, *, pipeline: RecallZeroPipeline, config: DetectorV2Config):
        self.pipeline = pipeline
        self.config = config
        self.coverage_engine = CoverageEngine()
        self.risk_engine = RiskEngineV2(config.risk)
        self._ordering_risk_config = _v2_to_v1_risk_config(config)

    def _coverage_to_metrics(self, coverage) -> CoverageMetrics:
        return CoverageMetrics(
            vehicle_coverage_score=coverage.vehicle_coverage_score,
            model_year_breadth_score=coverage.model_year_breadth_score,
            distinct_model_years=coverage.distinct_model_years,
            model_year_span=coverage.model_year_span,
            distinct_vin_prefixes=coverage.distinct_vin_prefixes,
            vin_evidence_ratio=coverage.vin_evidence_ratio,
            distinct_vehicles=coverage.distinct_vehicles,
            explanation=coverage.explanation,
        )


    @staticmethod
    def _severity_stub(signal):
        # The frozen pipeline does not persist the SeverityResult on DefectSignal. For v2
        # rescoring we reuse the already-validated consequence score carried by the v1
        # 'severity' risk factor, wrapped in a minimal stand-in exposing .score/.explanation.
        from types import SimpleNamespace

        factor = next((item for item in signal.risk.factors if item.name == "severity"), None)
        return SimpleNamespace(
            score=factor.score if factor else 0.0,
            explanation=factor.explanation if factor else "No validated severity evidence.",
        )

    async def analyze_vehicle(
        self,
        vehicle: Vehicle,
        *,
        cutoff_date: date | None = None,
        refresh: bool = False,
        max_complaints: int | None = None,
    ) -> DetectorV2Result:
        complaints, recalls = await self.pipeline.ingest(vehicle, refresh=refresh)
        cutoff = cutoff_date or date.today()
        visible = [item for item in complaints if item.received_date <= cutoff]
        if max_complaints is not None and len(visible) > max_complaints:
            visible = sorted(visible, key=lambda item: item.received_date)[-max_complaints:]
        signatures = await self.pipeline.extract_signatures(vehicle, visible, use_cache=not refresh)
        complaints_by_id = {item.odi_number: item for item in visible}
        run = await self.pipeline.analyze_records(
            vehicle=vehicle,
            complaints=visible,
            recalls=recalls,
            cutoff_date=cutoff,
            signatures=signatures,
            risk_config=self._ordering_risk_config,
            save=True,
        )
        return self._rescore_with_complaints(run, complaints_by_id)

    def _rescore_with_complaints(self, run: AnalysisRun, complaints_by_id) -> DetectorV2Result:
        signals_v2: list[SignalV2] = []
        for signal in run.signals:
            member_complaints = [
                complaints_by_id[item_id] for item_id in signal.cluster.member_ids if item_id in complaints_by_id
            ]
            coverage = self.coverage_engine.calculate(member_complaints, distinct_vehicles=1)
            risk = self.risk_engine.calculate(
                severity=self._severity_stub(signal),
                trend=signal.trend,
                recall_match=signal.recall_match,
                coverage=coverage,
                evidence_count=signal.cluster.evidence_count,
            )
            signals_v2.append(
                SignalV2(signal=signal, coverage=self._coverage_to_metrics(coverage), risk=risk)
            )
        signals_v2.sort(
            key=lambda item: (not item.risk.alert, -item.risk.final_score, item.signal.cluster.label)
        )
        return DetectorV2Result(
            run=run,
            signals_v2=tuple(signals_v2),
            trend_recent_window_days=self.config.trend.recent_window_days,
            trend_baseline_window_days=self.config.trend.baseline_window_days,
        )


def build_detector_v2(
    settings: Settings | None = None,
    *,
    use_nim: bool | None = None,
    config: DetectorV2Config | None = None,
) -> DetectorV2:
    settings = settings or get_settings()
    config = config or load_detector_v2_config()
    pipeline = build_pipeline(
        settings,
        use_nim=use_nim,
        risk_config=_v2_to_v1_risk_config(config),
    )
    # Override the frozen pipeline's trend engine with the v2 30/90 windows. The pipeline
    # stores the engine as an instance attribute, so no frozen source file is modified.
    pipeline.trend_engine = TrendEngine(
        recent_window_days=config.trend.recent_window_days,
        baseline_window_days=config.trend.baseline_window_days,
        persistence_weeks_to_full_score=config.trend.persistence_weeks_to_full_score,
    )
    return DetectorV2(pipeline=pipeline, config=config)