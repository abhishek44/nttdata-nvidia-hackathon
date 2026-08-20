from __future__ import annotations

from recallzero.models.domain import (
    AnalysisRun,
    DefectSignal,
    RiskAssessment,
    StrictModel,
)


class CoverageMetrics(StrictModel):
    """Serializable snapshot of the Detector v2 coverage measurement for a signal.

    Distinct VIN-prefix counts use NHTSA's 11-character VIN prefix (WMI + VDS + check
    digit + model year + plant code). They are a conservative lower bound on distinct
    physical vehicles, never an exact unique-VIN count.
    """

    vehicle_coverage_score: float
    model_year_breadth_score: float
    distinct_model_years: int
    model_year_span: int
    distinct_vin_prefixes: int
    vin_evidence_ratio: float
    distinct_vehicles: int
    explanation: str


class SignalV2(StrictModel):
    """A Detector v2 signal: the frozen v1 evidence lineage plus the v2 risk assessment
    and coverage breadth measurement. The underlying ``signal`` is produced by the frozen
    Detector v1 pipeline and is preserved unchanged for evidence traceability.
    """

    signal: DefectSignal
    coverage: CoverageMetrics
    risk: RiskAssessment  # v2 6-factor assessment (supersedes signal.risk for v2 reporting)


class DetectorV2Result(StrictModel):
    """Result of a Detector v2 per-vehicle analysis run."""

    run: AnalysisRun  # the frozen v1 run that produced the evidence lineage
    signals_v2: tuple[SignalV2, ...]
    detector_version: str = "v2"
    trend_recent_window_days: int = 30
    trend_baseline_window_days: int = 90