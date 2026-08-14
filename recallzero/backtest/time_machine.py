from __future__ import annotations

from datetime import date, timedelta

from recallzero.analytics.risk import score_risk
from recallzero.analytics.trends import trend_metrics
from recallzero.models import BacktestResult, EnrichedComplaint


def run_time_machine(
    items: list[EnrichedComplaint],
    campaign_number: str,
    recall_date: date,
    alert_threshold: float = 75.0,
    min_reports: int = 4,
    replay_step_days: int = 7,
) -> BacktestResult:
    pre_recall = [x for x in items if x.complaint.date_complaint_filed < recall_date]
    if not pre_recall:
        return BacktestResult(
            campaign_number=campaign_number,
            recall_date=recall_date,
            first_alert_date=None,
            lead_time_days=None,
            max_score=0.0,
            alert_threshold=alert_threshold,
            timeline=[],
        )

    start = min(x.complaint.date_complaint_filed for x in pre_recall)
    cutoff = start
    timeline: list[dict] = []
    first_alert = None
    max_score = 0.0

    while cutoff < recall_date:
        trend = trend_metrics(pre_recall, cutoff)
        risk = score_risk(pre_recall, trend, recall_exists=False)
        max_score = max(max_score, risk.final_score)
        is_alert = trend.total_reports >= min_reports and risk.final_score >= alert_threshold
        if is_alert and first_alert is None:
            first_alert = cutoff
        timeline.append(
            {
                "date": cutoff.isoformat(),
                "reports": trend.total_reports,
                "recent_reports": trend.recent_reports,
                "acceleration": trend.acceleration,
                "risk_score": risk.final_score,
                "alert": is_alert,
            }
        )
        cutoff += timedelta(days=replay_step_days)

    lead_time = (recall_date - first_alert).days if first_alert else None
    return BacktestResult(
        campaign_number=campaign_number,
        recall_date=recall_date,
        first_alert_date=first_alert,
        lead_time_days=lead_time,
        max_score=round(max_score, 1),
        alert_threshold=alert_threshold,
        timeline=timeline,
    )
