from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from recallzero.intelligence.clustering import canonical_family
from recallzero.intelligence.taxonomy import derive_recall_defect_families, families_related
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
    """Structured recall matcher with semantic text as supporting, not sole, evidence."""

    def __init__(self, match_threshold: float = 0.50):
        self.match_threshold = match_threshold

    @staticmethod
    def _cluster_text(cluster: ComplaintCluster, signatures: Sequence[FailureSignature]) -> str:
        signature_text = " ".join(signature.canonical_text() for signature in signatures)
        return normalize_match_text(
            f"{cluster.label} {cluster.system} {cluster.defect_family} {cluster.failure_mode} {signature_text}"
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
        recall_component = normalize_match_text(recall.component or "")
        if not recall_component:
            return 0.0
        cluster_family = canonical_family(cluster.system)
        recall_family = canonical_family(recall.component)
        if cluster_family != "UNKNOWN" and recall_family == cluster_family:
            return 1.0

        cluster_tokens = _token_set(cluster.system)
        recall_tokens = _token_set(recall.component or "")
        safety_tokens = {
            "brake", "braking", "steering", "powertrain", "propulsion", "battery", "electrical", "airbag",
            "restraint", "engine", "fuel", "visibility", "windshield", "glass", "camera", "speed", "control",
        }
        return _overlap(cluster_tokens & safety_tokens, recall_tokens & safety_tokens)

    @staticmethod
    def _subsystem_score(signatures: Sequence[FailureSignature], recall: Recall) -> float:
        left = set()
        for signature in signatures:
            left |= _token_set(" ".join(value for value in (signature.subsystem, signature.failure_mode) if value))
        right = _token_set(" ".join(value for value in (recall.component, recall.summary) if value))
        return _overlap(left, right)

    @staticmethod
    def _consequence_score(signatures: Sequence[FailureSignature], recall: Recall) -> float:
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
                "defect_family": 1.0,
                "component": 1.0,
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
                "defect_family": 0.0,
                "component": 0.0,
                "subsystem": 0.0,
                "consequence": 0.0,
                "semantic_lexical": 0.0,
                "final": 0.0,
            }

        vectorizer = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True)
        matrix = vectorizer.fit_transform([cluster_text, recall_text])
        semantic_lexical = float(cosine_similarity(matrix[0:1], matrix[1:2])[0, 0])
        component = self._component_score(cluster, recall)
        recall_families = derive_recall_defect_families(recall)
        defect_family = families_related(cluster.defect_family, recall_families)
        subsystem = self._subsystem_score(signatures, recall)
        consequence = self._consequence_score(signatures, recall)

        # Structured failure compatibility carries the most weight. Lexical similarity
        # supports the decision but cannot independently qualify a recall match.
        final = (
            0.34 * defect_family
            + 0.20 * component
            + 0.12 * subsystem
            + 0.14 * consequence
            + 0.20 * semantic_lexical
        )
        return {
            "explicit_campaign_reference": 0.0,
            "defect_family": round(defect_family, 4),
            "component": round(component, 4),
            "subsystem": round(subsystem, 4),
            "consequence": round(consequence, 4),
            "semantic_lexical": round(semantic_lexical, 4),
            "final": round(min(1.0, final), 4),
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
