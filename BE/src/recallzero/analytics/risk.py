from __future__ import annotations

from recallzero.models import EnrichedComplaint, RiskBreakdown, TrendMetrics


def score_risk(items: list[EnrichedComplaint], trend: TrendMetrics, recall_exists: bool = False) -> RiskBreakdown:
    visible = [x for x in items if x.complaint.date_complaint_filed <= trend.cutoff_date]
    indicators = {i for x in visible for i in x.signature.severity_indicators}
    moving_count = sum(x.signature.operating_state == "VEHICLE_MOVING" for x in visible)
    crash_count = sum(x.complaint.crash for x in visible)
    fire_count = sum(x.complaint.fire for x in visible)
    injury_count = sum(x.complaint.injuries > 0 for x in visible)

    severity = 25.0
    if "LOSS_OF_BRAKING" in indicators or "LOSS_OF_STEERING" in indicators:
        severity += 35
    if "LOSS_OF_MOTIVE_POWER" in indicators:
        severity += 25
    severity += min(15, moving_count * 2.5)
    severity += min(15, crash_count * 5 + fire_count * 7.5 + injury_count * 7.5)
    severity = min(100.0, severity)

    trend_score = min(100.0, max(0.0, (trend.acceleration - 1.0) * 35 + trend.recent_reports * 4))
    persistence = min(100.0, trend.persistence_weeks * 14.0)
    evidence = min(100.0, trend.total_reports * 8.0)
    recall_gap = 0.0 if recall_exists else 100.0

    final = (
        0.30 * severity
        + 0.25 * trend_score
        + 0.15 * persistence
        + 0.20 * evidence
        + 0.10 * recall_gap
    )
    reasons = [
        f"{trend.total_reports} visible supporting complaints by {trend.cutoff_date.isoformat()}",
        f"Complaint velocity is {trend.acceleration:.2f}x the preceding baseline",
        f"Signal persisted for {trend.persistence_weeks} consecutive week(s)",
    ]
    if moving_count:
        reasons.append(f"{moving_count} report(s) indicate the vehicle was moving")
    if not recall_exists:
        reasons.append("No matching recall is assumed visible at this cutoff")

    return RiskBreakdown(
        severity=round(severity, 1),
        trend=round(trend_score, 1),
        persistence=round(persistence, 1),
        evidence=round(evidence, 1),
        recall_gap=round(recall_gap, 1),
        final_score=round(final, 1),
        reasons=reasons,
    )
