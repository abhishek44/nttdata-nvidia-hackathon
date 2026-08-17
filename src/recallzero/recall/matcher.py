from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from recallzero.intelligence.clustering import canonical_family
from recallzero.intelligence.taxonomy import (
    consequence_related,
    derive_recall_axes,
    mechanism_related,
)
from recallzero.models import Complaint, ComplaintCluster, FailureSignature, Recall, RecallMatch


SYNONYM_REPLACEMENTS = {
    "motive power": "propulsion power",
    "power train": "powertrain propulsion",
    "braking effectiveness": "brake failure",
    "steering assist": "power steering",
    "air bags": "airbag restraint",
    "high voltage": "battery electrical high voltage",
    "junction box": "power distribution junction box",
    "contactor": "electrical switch contactor",
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "from", "is", "are", "was",
    "were", "be", "may", "can", "could", "vehicle", "vehicles", "driver", "drivers", "system", "systems",
}

RELATED_COMPONENTS = {
    frozenset(("ELECTRICAL SYSTEM", "POWER TRAIN")),
    frozenset(("ELECTRICAL SYSTEM", "FUEL/PROPULSION SYSTEM")),
    frozenset(("POWER TRAIN", "FUEL/PROPULSION SYSTEM")),
    frozenset(("DRIVER ASSISTANCE", "VEHICLE SPEED CONTROL")),
    frozenset(("SERVICE BRAKES", "VEHICLE SPEED CONTROL")),
}


def normalize_match_text(text: str) -> str:
    value = text.lower()
    for source, target in SYNONYM_REPLACEMENTS.items():
        value = value.replace(source, f"{source} {target}")
    return " ".join(re.findall(r"[a-z0-9]+", value))


def _token_set(text: str) -> set[str]:
    return {token for token in normalize_match_text(text).split() if len(token) > 2 and token not in STOPWORDS}


def _overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, min(len(left), len(right)))


def _campaign_aliases(campaign: str) -> set[str]:
    clean = re.sub(r"[^A-Z0-9]", "", campaign.upper())
    aliases = {clean}
    match = re.fullmatch(r"(\d{2}V\d{3})(\d{3})", clean)
    if match:
        aliases.add(match.group(1))
    return aliases


class RecallMatcher:
    """Dual-axis structured recall matcher.

    Root mechanism and driver-visible consequence are scored independently. Component
    compatibility acts as a gate so an unrelated visible recall cannot materially lower
    the recall-gap score only because of generic words such as "failure" or "power".
    """

    def __init__(self, match_threshold: float = 0.50):
        self.match_threshold = match_threshold

    @staticmethod
    def _cluster_text(cluster: ComplaintCluster, signatures: Sequence[FailureSignature]) -> str:
        signature_text = " ".join(signature.canonical_text() for signature in signatures)
        return normalize_match_text(
            f"{cluster.label} {cluster.system} {cluster.failure_mechanism} {cluster.consequence_family} "
            f"{cluster.defect_family} {cluster.failure_mode} {' '.join(cluster.source_systems)} {signature_text}"
        )

    @staticmethod
    def _recall_text(recall: Recall) -> str:
        return normalize_match_text(
            " ".join(
                value
                for value in (recall.component, recall.summary, recall.consequence, recall.remedy, recall.notes)
                if value
            )
        )

    @staticmethod
    def _explicit_campaign_reference(recall: Recall, complaints: Sequence[Complaint] | None) -> float:
        if not complaints:
            return 0.0
        aliases = _campaign_aliases(recall.campaign_number)
        for complaint in complaints:
            narrative = re.sub(r"[^A-Z0-9]", "", complaint.narrative.upper())
            if any(alias in narrative for alias in aliases):
                return 1.0
        return 0.0

    @staticmethod
    def _component_score(cluster: ComplaintCluster, recall: Recall) -> float:
        recall_family = canonical_family(recall.component)
        if recall_family == "UNKNOWN":
            return 0.0
        cluster_families = set(cluster.source_systems) or {cluster.system}
        cluster_families = {canonical_family(item) for item in cluster_families}
        cluster_families.discard("UNKNOWN")
        if recall_family in cluster_families:
            return 1.0
        if any(frozenset((recall_family, family)) in RELATED_COMPONENTS for family in cluster_families):
            return 0.65

        recall_tokens = _token_set(recall.component or "")
        best = 0.0
        safety_tokens = {
            "brake", "braking", "steering", "powertrain", "propulsion", "battery", "electrical", "airbag",
            "restraint", "engine", "fuel", "visibility", "windshield", "glass", "camera", "speed", "control",
        }
        for family in cluster_families:
            cluster_tokens = _token_set(family)
            best = max(best, _overlap(cluster_tokens & safety_tokens, recall_tokens & safety_tokens))
        return best

    @staticmethod
    def _subsystem_score(signatures: Sequence[FailureSignature], recall: Recall) -> float:
        left = set()
        for signature in signatures:
            left |= _token_set(" ".join(value for value in (signature.subsystem, signature.failure_mode) if value))
        right = _token_set(" ".join(value for value in (recall.component, recall.summary) if value))
        return _overlap(left, right)

    @staticmethod
    def _consequence_text_score(signatures: Sequence[FailureSignature], recall: Recall) -> float:
        left = set()
        for signature in signatures:
            left |= _token_set(" ".join(value for value in (signature.symptom, signature.consequence) if value))
        right = _token_set(" ".join(value for value in (recall.summary, recall.consequence) if value))
        return _overlap(left, right)

    def score_target_details(
        self,
        cluster: ComplaintCluster,
        signatures: Sequence[FailureSignature],
        recall: Recall,
        complaints: Sequence[Complaint] | None = None,
    ) -> dict[str, float]:
        explicit = self._explicit_campaign_reference(recall, complaints)
        if explicit:
            return {
                "explicit_campaign_reference": 1.0,
                "failure_mechanism": 1.0,
                "consequence_family": 1.0,
                "defect_family": 1.0,
                "component": 1.0,
                "component_compatible": 1.0,
                "subsystem": 1.0,
                "consequence": 1.0,
                "semantic_lexical": 1.0,
                "final": 1.0,
            }

        cluster_text = self._cluster_text(cluster, signatures)
        recall_text = self._recall_text(recall)
        if not recall_text:
            return {
                "explicit_campaign_reference": 0.0,
                "failure_mechanism": 0.0,
                "consequence_family": 0.0,
                "defect_family": 0.0,
                "component": 0.0,
                "component_compatible": 0.0,
                "subsystem": 0.0,
                "consequence": 0.0,
                "semantic_lexical": 0.0,
                "final": 0.0,
            }

        vectorizer = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True)
        matrix = vectorizer.fit_transform([cluster_text, recall_text])
        semantic_lexical = float(cosine_similarity(matrix[0:1], matrix[1:2])[0, 0])
        component = self._component_score(cluster, recall)
        recall_mechanisms, recall_consequences = derive_recall_axes(recall)
        mechanism_value = cluster.failure_mechanism
        consequence_value = cluster.consequence_family
        # Older serialized/manual clusters may only have defect_family populated.
        if mechanism_value == "OTHER" and cluster.defect_family in recall_mechanisms:
            mechanism_value = cluster.defect_family
        if consequence_value == "OTHER" and cluster.defect_family in recall_consequences:
            consequence_value = cluster.defect_family
        mechanism = mechanism_related(mechanism_value, recall_mechanisms)
        consequence_family = consequence_related(consequence_value, recall_consequences)
        defect_family = max(mechanism, consequence_family)
        subsystem = self._subsystem_score(signatures, recall)
        consequence = self._consequence_text_score(signatures, recall)
        component_compatible = 1.0 if component >= 0.35 else 0.0

        final = (
            0.30 * mechanism
            + 0.20 * consequence_family
            + 0.20 * component
            + 0.10 * subsystem
            + 0.10 * consequence
            + 0.10 * semantic_lexical
        )

        # Gating: a recall from an unrelated component domain should not reduce the
        # detector's recall-gap score through generic semantic overlap. A strong exact
        # mechanism match can retain a modest score for cross-domain wording, but it
        # cannot qualify a match without some component/subsystem support.
        if component_compatible == 0.0:
            if mechanism < 1.0:
                final *= 0.25
            else:
                final = min(final, 0.44)

        return {
            "explicit_campaign_reference": 0.0,
            "failure_mechanism": round(mechanism, 4),
            "consequence_family": round(consequence_family, 4),
            "defect_family": round(defect_family, 4),
            "component": round(component, 4),
            "component_compatible": component_compatible,
            "subsystem": round(subsystem, 4),
            "consequence": round(consequence, 4),
            "semantic_lexical": round(semantic_lexical, 4),
            "final": round(min(1.0, max(0.0, final)), 4),
        }

    def score_target(
        self,
        cluster: ComplaintCluster,
        signatures: Sequence[FailureSignature],
        recall: Recall,
        complaints: Sequence[Complaint] | None = None,
    ) -> float:
        return self.score_target_details(cluster, signatures, recall, complaints)["final"]

    def find_best_match(
        self,
        cluster: ComplaintCluster,
        signatures: Sequence[FailureSignature],
        recalls: Sequence[Recall],
        complaints: Sequence[Complaint] | None = None,
    ) -> RecallMatch:
        if not recalls:
            return RecallMatch(
                matched=False,
                score=0.0,
                reason="No recall visible at the analysis cutoff matched this complaint cluster.",
            )
        scored: list[tuple[float, Recall, dict[str, Any]]] = []
        for recall in recalls:
            details = self.score_target_details(cluster, signatures, recall, complaints)
            scored.append((details["final"], recall, details))
        scored.sort(key=lambda item: item[0], reverse=True)
        score, recall, details = scored[0]
        matched = score >= self.match_threshold
        if matched:
            reason = (
                f"Best visible recall match is campaign {recall.campaign_number} with structured score {score:.2f}; "
                "review campaign scope before treating the complaint pattern as covered."
            )
        else:
            reason = (
                f"No visible recall passed the structured match threshold. Best candidate was {recall.campaign_number} "
                f"at score {score:.2f}."
            )
        return RecallMatch(
            matched=matched,
            campaign_number=recall.campaign_number if matched else None,
            score=score,
            reason=reason,
            recall_date=recall.report_received_date if matched else None,
            score_breakdown={key: float(value) for key, value in details.items()},
        )
