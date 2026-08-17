from __future__ import annotations

import re
from collections.abc import Sequence

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from recallzero.models import ComplaintCluster, FailureSignature, Recall, RecallMatch


SYNONYM_REPLACEMENTS = {
    "motive power": "propulsion power",
    "power train": "powertrain propulsion",
    "braking effectiveness": "brake failure",
    "steering assist": "power steering",
    "air bags": "airbag restraint",
    "high voltage": "battery electrical high voltage",
}


def normalize_match_text(text: str) -> str:
    value = text.lower()
    for source, target in SYNONYM_REPLACEMENTS.items():
        value = value.replace(source, f"{source} {target}")
    return " ".join(re.findall(r"[a-z0-9]+", value))


class RecallMatcher:
    def __init__(self, match_threshold: float = 0.32):
        self.match_threshold = match_threshold

    @staticmethod
    def _cluster_text(cluster: ComplaintCluster, signatures: Sequence[FailureSignature]) -> str:
        signature_text = " ".join(signature.canonical_text() for signature in signatures)
        return normalize_match_text(f"{cluster.label} {cluster.system} {cluster.failure_mode} {signature_text}")

    @staticmethod
    def _recall_text(recall: Recall) -> str:
        return normalize_match_text(
            " ".join(
                value
                for value in (recall.component, recall.summary, recall.consequence, recall.remedy)
                if value
            )
        )

    @staticmethod
    def _component_overlap(cluster: ComplaintCluster, recall: Recall) -> float:
        cluster_tokens = set(normalize_match_text(f"{cluster.system} {cluster.failure_mode}").split())
        recall_tokens = set(normalize_match_text(recall.component or "").split())
        if not cluster_tokens or not recall_tokens:
            return 0.0
        safety_tokens = {
            "brake",
            "braking",
            "steering",
            "powertrain",
            "propulsion",
            "battery",
            "electrical",
            "airbag",
            "restraint",
            "engine",
            "fuel",
            "visibility",
        }
        left = cluster_tokens & safety_tokens
        right = recall_tokens & safety_tokens
        if not left or not right:
            return 0.0
        return len(left & right) / max(len(left), len(right))

    def score_target(
        self,
        cluster: ComplaintCluster,
        signatures: Sequence[FailureSignature],
        recall: Recall,
    ) -> float:
        cluster_text = self._cluster_text(cluster, signatures)
        recall_text = self._recall_text(recall)
        if not recall_text:
            return 0.0
        vectorizer = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True)
        matrix = vectorizer.fit_transform([cluster_text, recall_text])
        semantic_lexical = float(cosine_similarity(matrix[0:1], matrix[1:2])[0, 0])
        component = self._component_overlap(cluster, recall)
        return round(min(1.0, 0.78 * semantic_lexical + 0.22 * component), 4)

    def find_best_match(
        self,
        cluster: ComplaintCluster,
        signatures: Sequence[FailureSignature],
        recalls: Sequence[Recall],
    ) -> RecallMatch:
        if not recalls:
            return RecallMatch(
                matched=False,
                score=0.0,
                reason="No recall visible at the analysis cutoff matched this complaint cluster.",
            )
        scored = sorted(
            ((self.score_target(cluster, signatures, recall), recall) for recall in recalls),
            key=lambda item: item[0],
            reverse=True,
        )
        score, recall = scored[0]
        matched = score >= self.match_threshold
        if matched:
            reason = (
                f"Best visible recall match is campaign {recall.campaign_number} with similarity {score:.2f}; "
                "the cluster may be covered and should be reviewed against campaign scope."
            )
        else:
            reason = (
                f"No visible recall passed the match threshold. Best candidate was {recall.campaign_number} "
                f"at similarity {score:.2f}."
            )
        return RecallMatch(
            matched=matched,
            campaign_number=recall.campaign_number if matched else None,
            score=score,
            reason=reason,
            recall_date=recall.report_received_date if matched else None,
        )
