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
    context_by_complaint: dict[str, dict[str, str]] = field(default_factory=dict)


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
    patterns = (
        rf"(?:not|never|no|did not|does not|didn t|doesn t)\s+(?:\w+\s+){{0,4}}{re.escape(matched)}",
        rf"{re.escape(matched)}\s+(?:\w+\s+){{0,3}}(?:not|never)",
    )
    if any(re.search(pattern, text) for pattern in patterns):
        return True

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
    indicators are accepted only when the complaint explicitly supports the incident.
    Loss-of-motive-power additionally requires incident-scoped motion evidence so parked
    no-start complaints cannot be promoted to moving propulsion failures.
    """

    SEMANTIC_PATTERNS: dict[str, tuple[str, ...]] = {
        "loss_of_motive_power": (
            "loss of motive power",
            "lost motive power",
            "lost propulsion",
            "loss of propulsion",
            "lost all power",
            "lost all drive power",
            "lost drive power",
            "throttle went dead",
            "car turned itself off",
            "vehicle turned itself off",
            "vehicle shut down",
            "vehicle shutdown",
            "vehicle stalled",
            "unexpected vehicle stall",
            "car went dead",
            "vehicle went dead",
            "car died",
            "vehicle died",
            "vehicle dies",
            "car dies",
            "power cut out",
            "propulsion stopped",
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

    # Disabled/no-drive language is safety-relevant as loss of motive power only when
    # the incident also has explicit motion evidence. The same phrases in a parked
    # no-start complaint remain non-moving disablement evidence.
    MOTION_REQUIRED_DISABLED_PATTERNS: tuple[str, ...] = (
        "could not move vehicle",
        "could not move",
        "couldn t move",
        "would not move",
        "wouldn t move",
        "will not move",
        "unable to move",
        "could not drive",
        "would not drive",
        "wouldn t drive",
        "will not drive",
        "would not go forward",
        "could not go forward",
        "could not shift into drive",
        "would not shift into drive",
        "could not enter drive",
        "would not enter drive",
    )

    MOTION_PATTERNS: tuple[str, ...] = (
        "while driving",
        "was driving",
        "started driving",
        "driving out of",
        "driving our",
        "driving my",
        "driving the",
        "driving this",
        "driving down",
        "driving on",
        "while traveling",
        "while travelling",
        "travelling at",
        "traveling at",
        "travelling from",
        "traveling from",
        "driving at",
        "while reversing",
        "while entering the freeway",
        "while entering the fwy",
        "while braking",
        "while operating the vehicle",
        "in motion",
        "had to immediately pull over",
        "had to pull over",
        "pulled over",
        "coast to the side",
        "coasted to the side",
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
        "turned itself off",
        "stalled",
        "went dead",
        "vehicle died",
        "car died",
        "vehicle dies",
        "car dies",
        "would not",
        "wouldn t",
        "could not",
        "couldn t",
        "driving",
        "will not",
        "cannot",
        "unable to",
        "accelerat",
        "brak",
        "steer",
        "crash",
        "warning",
        "error",
        "locked",
        "stuck",
        "throttle",
        "pull over",
        "coast",
    )

    PARKED_EVENT_CUES: tuple[str, ...] = (
        "while parked",
        "was parked",
        "car was parked",
        "vehicle was parked",
        "parked in",
        "parked at",
        "parking lot",
        "parking structure",
        "upon returning to my vehicle",
        "returning to my vehicle",
        "after a brief stop",
    )

    @classmethod
    def _incident_sentences(cls, complaint: Complaint, signature: FailureSignature) -> list[str]:
        sentences = _sentences(complaint.narrative)
        incident = [sentence for sentence in sentences if any(cue in _norm(sentence) for cue in cls.INCIDENT_CUES)]
        if incident:
            return incident
        return sentences[:2]

    @classmethod
    def _parked_event_evidence(cls, complaint: Complaint, signature: FailureSignature) -> str | None:
        """Return evidence that the *failure event* is parked, not merely that parking is mentioned.

        0.3.3a2 already carried parked-event cues but did not use them.  The a2 guard
        intentionally requires the extracted operating state to agree with a parked cue.
        This makes the veto narrow: a later sentence saying the driver parked after an
        in-motion failure does not erase valid motion evidence from that failure.
        """

        state = _norm(signature.operating_state)
        if "parked" not in state:
            return None
        for sentence in cls._incident_sentences(complaint, signature):
            normalized = _norm(sentence)
            for phrase in cls.PARKED_EVENT_CUES:
                if phrase in normalized:
                    return f"Failure event is parked: {phrase!r}; operating_state={signature.operating_state!r}"
        return None

    @classmethod
    def _parked_event(cls, complaint: Complaint, signature: FailureSignature) -> bool:
        return cls._parked_event_evidence(complaint, signature) is not None

    @classmethod
    def _motion_evidence(cls, complaint: Complaint, signature: FailureSignature) -> str | None:
        # A parked failure can contain background/history text such as "while driving".
        # Do not let that unrelated sentence promote a parked no-start into an
        # in-motion event.  The guard is intentionally state+cues scoped rather than
        # a document-wide ban on the word "parked".
        if cls._parked_event(complaint, signature):
            return None

        for sentence in cls._incident_sentences(complaint, signature):
            normalized = _norm(sentence)
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

    def _validate_indicators_with_context(
        self, complaint: Complaint, signature: FailureSignature
    ) -> tuple[dict[str, str], dict[str, str]]:
        evidence: dict[str, str] = {}
        context: dict[str, str] = {}
        parked = self._parked_event_evidence(complaint, signature)
        if parked:
            context["event_state_source"] = "suppressed_by_parked_event"
            context["parked_event_evidence"] = parked

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
            context["event_state_source"] = "incident_motion_context"
        elif "event_state_source" not in context and any(
            key in evidence for key in ("crash_reported", "injury_reported", "fatality_reported", "fire_or_thermal_event")
        ):
            context["event_state_source"] = "structured_nhtsa"
        elif "event_state_source" not in context:
            context["event_state_source"] = "no_validated_motion_context"

        incident_sentences = self._incident_sentences(complaint, signature)
        for indicator, patterns in self.SEMANTIC_PATTERNS.items():
            if indicator in SOURCE_ONLY_INDICATORS:
                continue
            # Loss of motive power is specifically an in-motion consequence. A parked
            # no-start/no-drive incident must not receive this severity indicator.
            if indicator == "loss_of_motive_power" and not motion:
                continue
            for sentence in incident_sentences:
                matched = _first_match(sentence, patterns)
                if matched and not _hypothetical_or_negated(sentence, matched):
                    evidence[indicator] = f"Incident narrative support: {matched!r}"
                    break

        if motion and "loss_of_motive_power" not in evidence:
            for sentence in incident_sentences:
                matched = _first_match(sentence, self.MOTION_REQUIRED_DISABLED_PATTERNS)
                if matched and not _hypothetical_or_negated(sentence, matched):
                    evidence["loss_of_motive_power"] = (
                        f"Incident narrative support with motion context: {matched!r}"
                    )
                    break

        return dict(sorted(evidence.items())), dict(sorted(context.items()))

    def validate_indicators(self, complaint: Complaint, signature: FailureSignature) -> dict[str, str]:
        evidence, _context = self._validate_indicators_with_context(complaint, signature)
        return evidence

    def validate_context(self, complaint: Complaint, signature: FailureSignature) -> dict[str, str]:
        _evidence, context = self._validate_indicators_with_context(complaint, signature)
        return context

    def calculate(self, complaints: Sequence[Complaint], signatures: Sequence[FailureSignature]) -> SeverityResult:
        signatures_by_id = {item.complaint_id: item for item in signatures}
        counts: Counter[str] = Counter()
        rejected: Counter[str] = Counter()
        evidence_by_complaint: dict[str, dict[str, str]] = {}
        context_by_complaint: dict[str, dict[str, str]] = {}

        for complaint in complaints:
            signature = signatures_by_id.get(complaint.odi_number)
            if signature is None:
                continue
            evidence, context = self._validate_indicators_with_context(complaint, signature)
            evidence_by_complaint[complaint.odi_number] = evidence
            context_by_complaint[complaint.odi_number] = context
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
                context_by_complaint=context_by_complaint,
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
            context_by_complaint=context_by_complaint,
        )
