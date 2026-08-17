from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date, timedelta

from recallzero.models import Complaint, TrendMetrics


class TrendEngine:
    def __init__(
        self,
        recent_window_days: int = 28,
        baseline_window_days: int = 84,
        persistence_weeks_to_full_score: int = 4,
    ):
        self.recent_window_days = recent_window_days
        self.baseline_window_days = baseline_window_days
        self.persistence_weeks_to_full_score = persistence_weeks_to_full_score

    @staticmethod
    def _count_between(complaints: Sequence[Complaint], start: date, end: date) -> int:
        return sum(1 for complaint in complaints if start <= complaint.received_date <= end)

    def _persistence_weeks(self, complaints: Sequence[Complaint], cutoff_date: date) -> int:
        count = 0
        end = cutoff_date
        for _ in range(12):
            start = end - timedelta(days=6)
            if self._count_between(complaints, start, end) == 0:
                break
            count += 1
            end = start - timedelta(days=1)
        return count

    def calculate(self, complaints: Sequence[Complaint], cutoff_date: date) -> TrendMetrics:
        visible = [complaint for complaint in complaints if complaint.received_date <= cutoff_date]
        recent_start = cutoff_date - timedelta(days=self.recent_window_days - 1)
        baseline_end = recent_start - timedelta(days=1)
        baseline_start = baseline_end - timedelta(days=self.baseline_window_days - 1)

        recent_count = self._count_between(visible, recent_start, cutoff_date)
        baseline_count = self._count_between(visible, baseline_start, baseline_end)
        recent_rate = recent_count * (28.0 / self.recent_window_days)
        baseline_rate = baseline_count * (28.0 / self.baseline_window_days)

        # Add-one smoothing avoids an infinite ratio while preserving the direction of a new signal.
        trend_ratio = (recent_rate + 1.0) / (baseline_rate + 1.0)
        acceleration_score = min(100.0, max(0.0, 35.0 * math.log2(max(1.0, trend_ratio))))
        persistence_weeks = self._persistence_weeks(visible, cutoff_date)
        persistence_score = min(100.0, 100.0 * persistence_weeks / self.persistence_weeks_to_full_score)
        evidence_score = min(100.0, 100.0 * (1.0 - math.exp(-len(visible) / 6.0)))

        return TrendMetrics(
            cutoff_date=cutoff_date,
            recent_window_days=self.recent_window_days,
            baseline_window_days=self.baseline_window_days,
            recent_count=recent_count,
            baseline_count=baseline_count,
            recent_rate_per_28d=round(recent_rate, 4),
            baseline_rate_per_28d=round(baseline_rate, 4),
            trend_ratio=round(trend_ratio, 4),
            acceleration_score=round(acceleration_score, 2),
            persistence_weeks=persistence_weeks,
            persistence_score=round(persistence_score, 2),
            evidence_score=round(evidence_score, 2),
        )
