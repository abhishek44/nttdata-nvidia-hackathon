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


def _sentences(text: str) -> list[str]:
    # Keep context windows small enough to distinguish the incident from background,
    # owner speculation, or a later sentence about what could have happened.
    parts = re.split(r"(?<=[.!?;])\s+|\n+", text)
    return [part.strip() for part in parts if part.strip()]


def _first_match(text: str, patterns: tuple[str, ...]) -> str | None:
    normalized = _norm(text)
    for pattern in patterns:
        if pattern in normalized:
            return pattern
    return None


def _hypothetical_or_negated(sentence: str, matched: str) -> bool:
    text = _norm(sentence)
    # Direct negation near the matched phrase.
    patterns = (
        rf"(?:not|never|no|did not|does not|didn t|doesn t)\s+(?:\w+\s+){{0,4}}{re.escape(matched)}",
        rf"{re.escape(matched)}\s+(?:\w+\s+){{0,3}}(?:not|never)",
    )
    if any(re.search(pattern, text) for pattern in patterns):
        return True

    # Speculative language should not become an observed safety event. Exempt common
    # factual constructions such as "would not start" / "could not shift".
    factual_not = any(
        phrase in text
        for phrase in (
            "would not start",
            "could not start",
            "would not move",
            "could not move",
            "would not shift",
            "could not shift",
            "would not stop",
            "could not stop",
        )
    )
    if not factual_not and any(
        phrase in text
        for phrase in (
            "could cause",
            "may cause",
            "might cause",
            "could result",
            "may result",
            "might result",
            "potential for",
            "potentially",
            "afraid of",
            "fear of",
            "worried that",
            "concern that",
            "if this happens",
            "if it happens",
        )
    ):
        return True
    return False


class SeverityEngine:
    """Deterministic event-scoped safety-indicator validator and scorer.

    Structured NHTSA crash/injury/fatality/fire fields remain source truth. Semantic
    indicators are accepted only when explicit language occurs in an incident sentence,
    not merely somewhere in the complaint background. This avoids treating statements
    such as "luckily it did not fail while driving" as an in-motion failure.
    """

    SEMANTIC_PATTERNS: dict[str, tuple[str, ...]] = {
        "loss_of_motive_power": (
            "loss of motive power",
            "lost motive power",
            "lost propulsion",
            "loss of propulsion",
            "lost all power",
            "throttle went dead",
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
            "melted",
        ),
    }

    MOTION_PATTERNS: tuple[str, ...] = (
        "while driving",
        "was driving",
        "while traveling",
        "while travelling",
        "travelling at",
        "traveling at",
        "driving at",
        "while reversing",
        "while braking",
        "while operating the vehicle",
        "in motion",
    )

    INCIDENT_CUES: tuple[str, ...] = (
        "fail",
        "fault",
        "malfunction",
        "stop safely",
        "lost",
        "loss",
        "shut down",
        "shutdown",
        "stalled",
        "went dead",
        "would not",
        "could not",
        "accelerat",
        "brak",
        "steer",
        "crash",
        "warning",
        "error",
        "locked",
        "stuck",
    )

    @classmethod
    def _incident_sentences(cls, complaint: Complaint, signature: FailureSignature) -> list[str]:
        sentences = _sentences(complaint.narrative)
        incident = [sentence for sentence in sentences if any(cue in _norm(sentence) for cue in cls.INCIDENT_CUES)]
        if incident:
            return incident
        # Sparse complaints can be a single fragment without a conventional verb.
        return sentences[:2]

    @classmethod
    def _motion_evidence(cls, complaint: Complaint, signature: FailureSignature) -> str | None:
        for sentence in cls._incident_sentences(complaint, signature):
            normalized = _norm(sentence)
            # Explicit negative/historical phrases are common in safety complaints.
            if any(
                phrase in normalized
                for phrase in (
                    "not while driving",
                    "did not happen while driving",
                    "didn t happen while driving",
                    "did not fail while driving",
                    "didn t fail while driving",
                    "fortunately not while driving",
                    "luckily not while driving",
                    "if this happened while driving",
                    "if it happened while driving",
                )
            ):
                continue
            matched = _first_match(sentence, cls.MOTION_PATTERNS)
            if matched and not _hypothetical_or_negated(sentence, matched):
                return f"Incident sentence indicates motion: {matched!r}"
        return None

    def validate_indicators(self, complaint: Complaint, signature: FailureSignature) -> dict[str, str]:
        evidence: dict[str, str] = {}

        if complaint.crash:
            evidence["crash_reported"] = "NHTSA structured crash flag=true"
        if complaint.injuries > 0:
            evidence["injury_reported"] = f"NHTSA structured injuries={complaint.injuries}"
        if complaint.deaths > 0:
            evidence["fatality_reported"] = f"NHTSA structured deaths={complaint.deaths}"
        if complaint.fire:
            evidence["fire_or_thermal_event"] = "NHTSA structured fire flag=true"

        motion = self._motion_evidence(complaint, signature)
        if motion:
            evidence["vehicle_in_motion"] = motion

        incident_sentences = self._incident_sentences(complaint, signature)
        for indicator, patterns in self.SEMANTIC_PATTERNS.items():
            if indicator in SOURCE_ONLY_INDICATORS:
                continue
            for sentence in incident_sentences:
                matched = _first_match(sentence, patterns)
                if matched and not _hypothetical_or_negated(sentence, matched):
                    evidence[indicator] = f"Incident narrative support: {matched!r}"
                    break

        return dict(sorted(evidence.items()))

    def calculate(self, complaints: Sequence[Complaint], signatures: Sequence[FailureSignature]) -> SeverityResult:
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
            "other indicators require event-scoped narrative support and are counted at most once per complaint."
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
