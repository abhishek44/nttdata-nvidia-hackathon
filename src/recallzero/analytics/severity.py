from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from recallzero.models import Complaint, FailureSignature


INDICATOR_SCORES: dict[str, float] = {
    "fatality_reported": 100.0,
    "loss_of_control": 94.0,
    "injury_reported": 92.0,
    "loss_of_braking": 91.0,
    "loss_of_steering": 89.0,
    "restraint_failure": 88.0,
    "unintended_acceleration": 87.0,
    "unintended_braking": 86.0,
    "fire_or_thermal_event": 85.0,
    "crash_reported": 82.0,
    "loss_of_motive_power": 80.0,
    "vehicle_in_motion": 35.0,
}


@dataclass(slots=True)
class SeverityResult:
    score: float
    explanation: str
    indicator_counts: dict[str, int]


class SeverityEngine:
    def calculate(
        self,
        complaints: Sequence[Complaint],
        signatures: Sequence[FailureSignature],
    ) -> SeverityResult:
        """Score supported safety indicators without double-counting one record.

        An extractor may copy a source crash/fire/injury field into the signature. Source
        flags and extracted labels are therefore merged per ODI record before aggregate
        counts are calculated.
        """
        indicators_by_complaint: dict[str, set[str]] = {
            signature.complaint_id: set(signature.severity_indicators) for signature in signatures
        }
        for complaint in complaints:
            indicators = indicators_by_complaint.setdefault(complaint.odi_number, set())
            if complaint.crash:
                indicators.add("crash_reported")
            if complaint.fire:
                indicators.add("fire_or_thermal_event")
            if complaint.injuries:
                indicators.add("injury_reported")
            if complaint.deaths:
                indicators.add("fatality_reported")

        counts: Counter[str] = Counter()
        for indicators in indicators_by_complaint.values():
            counts.update(indicators)

        if not counts:
            return SeverityResult(
                score=15.0,
                explanation="No explicit high-severity indicator was extracted.",
                indicator_counts={},
            )

        scored = sorted(
            ((INDICATOR_SCORES.get(name, 20.0), name, count) for name, count in counts.items()),
            reverse=True,
        )
        highest_score, _highest_name, highest_count = scored[0]
        repetition_bonus = min(10.0, max(0, highest_count - 1) * 2.0)
        cross_indicator_bonus = min(6.0, max(0, len(scored) - 1) * 1.5)
        score = min(100.0, highest_score + repetition_bonus + cross_indicator_bonus)
        top_text = ", ".join(f"{name} ({count})" for _, name, count in scored[:4])
        explanation = (
            f"Severity is driven by supported indicators: {top_text}. "
            "Each indicator is counted at most once per complaint. Crash/fire/injury flags are "
            "treated as associations reported in complaints, not proven causation."
        )
        return SeverityResult(score=round(score, 2), explanation=explanation, indicator_counts=dict(counts))
