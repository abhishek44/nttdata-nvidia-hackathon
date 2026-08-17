from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

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

SOURCE_ONLY_INDICATORS = {
    "crash_reported",
    "injury_reported",
    "fatality_reported",
}


@dataclass(slots=True)
class SeverityResult:
    score: float
    explanation: str
    indicator_counts: dict[str, int]
    rejected_indicator_counts: dict[str, int] = field(default_factory=dict)
    evidence_by_complaint: dict[str, dict[str, str]] = field(default_factory=dict)


def _norm(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _first_match(text: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        if pattern in text:
            return pattern
    return None


class SeverityEngine:
    """Deterministic safety-indicator validator and scorer.

    Crash, injury, fatality, and fire source flags are treated as structured NHTSA
    evidence. Semantic indicators from the LLM are accepted only when the complaint
    narrative contains explicit supporting language. This prevents hypothetical text
    such as "could cause a crash" from being converted into a reported crash.
    """

    SEMANTIC_PATTERNS: dict[str, tuple[str, ...]] = {
        "loss_of_motive_power": (
            "loss of motive power",
            "lost motive power",
            "lost propulsion",
            "loss of propulsion",
            "lost all power while driving",
            "throttle went dead",
            "no acceleration",
            "car turned itself off",
            "vehicle shut down",
            "vehicle shutdown",
            "vehicle stalled",
            "car went dead",
        ),
        "loss_of_braking": (
            "loss of braking",
            "lost all regenerative braking",
            "brakes failed",
            "brake failure",
            "power brakes failed",
            "failed to stop",
            "did not slow down",
            "no brakes",
        ),
        "loss_of_steering": (
            "power steering failed",
            "loss of steering",
            "no power steering",
            "unable to steer",
            "steering locked",
            "steering lock",
        ),
        "unintended_acceleration": (
            "unintended acceleration",
            "unexpected acceleration",
            "accelerated on its own",
            "independently accelerated",
            "accelerated independently",
            "suddenly accelerated",
        ),
        "unintended_braking": (
            "unintended braking",
            "unexpected braking",
            "braked on its own",
            "independently braked",
            "brakes suddenly applied",
            "phantom braking",
        ),
        "loss_of_control": (
            "loss of control",
            "lost control",
            "could not control",
            "unable to control",
        ),
        "restraint_failure": (
            "airbag did not deploy",
            "air bag did not deploy",
            "seat belt failed",
            "seatbelt failed",
            "retractor failure",
        ),
        "fire_or_thermal_event": (
            "caught fire",
            "vehicle fire",
            "smoke coming",
            "smoke from",
            "burning smell",
            "overheated",
            "overheating",
            "thermal event",
        ),
    }

    MOTION_PATTERNS: tuple[str, ...] = (
        "while driving",
        "was driving",
        "driving",
        "driving at",
        "driving on",
        "while traveling",
        "travelling",
        "traveling",
        "highway",
        "freeway",
        "mph",
        "in motion",
        "while reversing",
        "while braking",
        "while operating the vehicle",
        "on the road",
    )

    def validate_indicators(
        self,
        complaint: Complaint,
        signature: FailureSignature,
    ) -> dict[str, str]:
        text = _norm(complaint.narrative)
        evidence: dict[str, str] = {}

        # Structured source truth. These do not depend on LLM labels.
        if complaint.crash:
            evidence["crash_reported"] = "NHTSA structured crash flag=true"
        if complaint.injuries > 0:
            evidence["injury_reported"] = f"NHTSA structured injuries={complaint.injuries}"
        if complaint.deaths > 0:
            evidence["fatality_reported"] = f"NHTSA structured deaths={complaint.deaths}"
        if complaint.fire:
            evidence["fire_or_thermal_event"] = "NHTSA structured fire flag=true"

        candidate_indicators = set(signature.severity_indicators)

        # vehicle_in_motion must have explicit motion support. A model label alone is
        # insufficient because many prior false positives occurred on parked events.
        motion_match = _first_match(text, self.MOTION_PATTERNS)
        if motion_match:
            evidence["vehicle_in_motion"] = f"Narrative explicitly indicates motion: {motion_match!r}"

        for indicator, patterns in self.SEMANTIC_PATTERNS.items():
            if indicator in SOURCE_ONLY_INDICATORS:
                continue
            # Structured fire is already handled; semantic thermal language can also
            # support a thermal event even when the source flag is absent.
            if indicator not in candidate_indicators and indicator != "fire_or_thermal_event":
                continue
            matched = _first_match(text, patterns)
            if matched:
                evidence[indicator] = f"Narrative support: {matched!r}"

        # Conservative deterministic recovery: if the LLM missed a strongly explicit
        # semantic indicator, the validator may still add it from the narrative. This
        # keeps the numerical scorer deterministic and avoids dependence on one model
        # wording choice.
        for indicator, patterns in self.SEMANTIC_PATTERNS.items():
            if indicator in SOURCE_ONLY_INDICATORS or indicator in evidence:
                continue
            matched = _first_match(text, patterns)
            if matched:
                evidence[indicator] = f"Narrative support: {matched!r}"

        return dict(sorted(evidence.items()))

    def calculate(
        self,
        complaints: Sequence[Complaint],
        signatures: Sequence[FailureSignature],
    ) -> SeverityResult:
        signatures_by_id = {item.complaint_id: item for item in signatures}
        counts: Counter[str] = Counter()
        rejected: Counter[str] = Counter()
        evidence_by_complaint: dict[str, dict[str, str]] = {}

        for complaint in complaints:
            signature = signatures_by_id.get(complaint.odi_number)
            if signature is None:
                continue
            evidence = self.validate_indicators(complaint, signature)
            evidence_by_complaint[complaint.odi_number] = evidence
            counts.update(evidence.keys())
            for candidate in signature.severity_indicators:
                if candidate not in evidence:
                    rejected[candidate] += 1

        if not counts:
            return SeverityResult(
                score=15.0,
                explanation="No explicitly supported high-severity indicator was validated.",
                indicator_counts={},
                rejected_indicator_counts=dict(rejected),
                evidence_by_complaint=evidence_by_complaint,
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
            f"Severity is driven by validated indicators: {top_text}. "
            "Crash/injury/fatality/fire source flags come from NHTSA structured fields; "
            "other indicators require explicit narrative support and are counted at most once per complaint."
        )
        if rejected:
            explanation += f" Rejected {sum(rejected.values())} unsupported LLM indicator occurrence(s)."
        return SeverityResult(
            score=round(score, 2),
            explanation=explanation,
            indicator_counts=dict(counts),
            rejected_indicator_counts=dict(rejected),
            evidence_by_complaint=evidence_by_complaint,
        )
