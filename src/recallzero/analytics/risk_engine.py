from __future__ import annotations

from recallzero.analytics.severity import SeverityResult
from recallzero.config import RiskConfig
from recallzero.models import RecallMatch, RiskAssessment, RiskFactor, SignalLevel, TrendMetrics


class RiskEngine:
    def __init__(self, config: RiskConfig):
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
        evidence_count: int,
    ) -> RiskAssessment:
        recall_gap_score = round(100.0 * (1.0 - recall_match.score), 2)
        values = {
            "severity": (
                severity.score,
                self.config.weights.severity,
                severity.explanation,
            ),
            "trend": (
                trend.acceleration_score,
                self.config.weights.trend,
                f"Recent 28-day-equivalent rate is {trend.recent_rate_per_28d:.2f} versus "
                f"{trend.baseline_rate_per_28d:.2f} in the baseline ({trend.trend_ratio:.2f}x smoothed ratio).",
            ),
            "persistence": (
                trend.persistence_score,
                self.config.weights.persistence,
                f"The signal appears in {trend.persistence_weeks} consecutive recent week(s).",
            ),
            "evidence": (
                trend.evidence_score,
                self.config.weights.evidence,
                f"The cluster contains {evidence_count} independent complaint record(s).",
            ),
            "recall_gap": (
                recall_gap_score,
                self.config.weights.recall_gap,
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
        rationale = (
            f"{level.value} priority ({final_score:.1f}/100): severity {severity.score:.1f}, "
            f"trend {trend.acceleration_score:.1f}, persistence {trend.persistence_score:.1f}, "
            f"evidence {trend.evidence_score:.1f}, recall gap {recall_gap_score:.1f}. "
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
