from __future__ import annotations

from datetime import date, timedelta

from recallzero.models import EnrichedComplaint, TrendMetrics


def trend_metrics(
    items: list[EnrichedComplaint],
    cutoff: date,
    recent_days: int = 28,
    baseline_days: int = 84,
) -> TrendMetrics:
    visible = [x for x in items if x.complaint.date_complaint_filed <= cutoff]
    recent_start = cutoff - timedelta(days=recent_days - 1)
    baseline_start = recent_start - timedelta(days=baseline_days)
    recent = [x for x in visible if x.complaint.date_complaint_filed >= recent_start]
    baseline = [x for x in visible if baseline_start <= x.complaint.date_complaint_filed < recent_start]
    recent_rate = len(recent) / (recent_days / 7)
    baseline_rate = len(baseline) / (baseline_days / 7)
    acceleration = recent_rate / max(baseline_rate, 0.25)

    # Persistence = number of consecutive weeks ending at cutoff with >=1 complaint.
    persistence = 0
    for week in range(8):
        end = cutoff - timedelta(days=7 * week)
        start = end - timedelta(days=6)
        if any(start <= x.complaint.date_complaint_filed <= end for x in visible):
            persistence += 1
        else:
            break

    return TrendMetrics(
        cutoff_date=cutoff,
        total_reports=len(visible),
        recent_reports=len(recent),
        baseline_reports=len(baseline),
        recent_weekly_rate=round(recent_rate, 3),
        baseline_weekly_rate=round(baseline_rate, 3),
        acceleration=round(acceleration, 3),
        persistence_weeks=persistence,
    )
