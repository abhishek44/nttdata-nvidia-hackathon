from __future__ import annotations

from recallzero.analytics.coverage import CoverageResult
from recallzero.analytics.risk_v2_config import RiskConfigV2
from recallzero.analytics.severity import SeverityResult
from recallzero.models import RecallMatch, RiskAssessment, RiskFactor, SignalLevel, TrendMetrics

_TIER_LABELS = (
    ("Monitor", 0.0),
    ("Investigate", 40.0),
    ("High priority", 70.0),
    ("Immediate engineering review", 85.0),
)


def _tier_label(score: float) -> str:
    label = _TIER_LABELS[0][0]
    for name, floor in _TIER_LABELS:
        if score >= floor:
            label = name
    return label


class RiskEngineV2:
    """Deterministic Detector v2 risk scoring over six independently-scored factors.

    The language model is never involved in the score; every factor is computed from
    deterministic evidence metrics so the calculation is auditable and reproducible.

    Factors (weight):
      - volume_increase      0.25  recent-vs-baseline complaint growth (trend)
      - vehicle_coverage     0.20  evidence volume + VIN-prefix + cross-vehicle breadth
      - safety_consequence   0.20  validated crash/fire/injury/death consequence
      - persistence          0.15  active-week persistence of the pattern
      - model_year_breadth   0.10  distinct model-year span
      - no_matching_recall   0.10  recall gap (no matching recall campaign visible)
    """

    def __init__(self, config: RiskConfigV2):
        self.config = config

    def _level(self, score: float) -> SignalLevel:
        if score >= self.config.critical_threshold:
            return SignalLevel.CRITICAL
        if score >= self.config.high_threshold:
            return SignalLevel.HIGH
        if score >= self.config.medium_threshold:
            return SignalLevel.MEDIUM
        return SignalLevel.LOW

    def calculate(
        self,
        *,
        severity: SeverityResult,
        trend: TrendMetrics,
        recall_match: RecallMatch,
        coverage: CoverageResult,
        evidence_count: int,
    ) -> RiskAssessment:
        no_recall_score = round(100.0 * (1.0 - recall_match.score), 2)
        volume_explanation = (
            f"Recent {trend.recent_window_days}-day activity is {trend.recent_rate_per_28d:.2f} complaints/28d "
            f"versus {trend.baseline_rate_per_28d:.2f} in the {trend.baseline_window_days}-day baseline "
            f"({trend.trend_ratio:.2f}x smoothed ratio; {trend.recent_count} recent vs {trend.baseline_count} baseline)."
        )
        values = {
            "volume_increase": (
                trend.acceleration_score,
                self.config.weights.volume_increase,
                volume_explanation,
            ),
            "vehicle_coverage": (
                coverage.vehicle_coverage_score,
                self.config.weights.vehicle_coverage,
                coverage.explanation,
            ),
            "safety_consequence": (
                severity.score,
                self.config.weights.safety_consequence,
                severity.explanation,
            ),
            "persistence": (
                trend.persistence_score,
                self.config.weights.persistence,
                f"Persistence combines {trend.active_weeks_recent_4}/4 active recent weeks with a "
                f"maximum {trend.max_consecutive_weeks_recent_8}-week active run in the last eight weeks.",
            ),
            "model_year_breadth": (
                coverage.model_year_breadth_score,
                self.config.weights.model_year_breadth,
                f"Evidence spans {coverage.distinct_model_years} distinct model year(s) "
                f"(span {coverage.model_year_span}).",
            ),
            "no_matching_recall": (
                no_recall_score,
                self.config.weights.no_matching_recall,
                recall_match.reason,
            ),
        }
        factors = tuple(
            RiskFactor(
                name=name,
                score=round(score, 2),
                weight=weight,
                contribution=round(score * weight, 2),
                explanation=explanation,
            )
            for name, (score, weight, explanation) in values.items()
        )
        final_score = round(sum(factor.contribution for factor in factors), 2)
        alert = final_score >= self.config.alert_threshold and evidence_count >= self.config.minimum_evidence
        level = self._level(final_score)
        factor_summary = ", ".join(
            f"{factor.name} {factor.score:.1f}/{int(factor.weight * 100)}" for factor in factors
        )
        rationale = (
            f"{_tier_label(final_score)} ({final_score:.1f}/100). "
            f"Breakdown: {factor_summary}. "
            f"Alert gate {'passed' if alert else 'not passed'} at {self.config.alert_threshold:.1f}/100 "
            f"with minimum evidence {self.config.minimum_evidence}."
        )
        return RiskAssessment(
            final_score=final_score,
            level=level,
            factors=factors,
            threshold=self.config.alert_threshold,
            minimum_evidence=self.config.minimum_evidence,
            alert=alert,
            rationale=rationale,
        )