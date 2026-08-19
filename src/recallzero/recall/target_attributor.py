from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from recallzero.intelligence.embeddings import Embedder
from recallzero.models import Complaint, ComplaintCluster, EmbeddingMethod, FailureSignature, Recall
from recallzero.recall.matcher import RecallMatcher


@dataclass(slots=True, frozen=True)
class TargetAttributionScore:
    """Evaluation-only target-attribution comparison.

    ``baseline`` is the frozen structured/TF-IDF score from ``RecallMatcher``.
    ``experimental`` replaces only the 10% ``semantic_lexical`` term with a
    meaning-aware embedding cosine. All structured axes, weights, component gate,
    and target threshold remain unchanged.
    """

    baseline: dict[str, float]
    embedding_similarity: float
    experimental: dict[str, float]
    embedding_method: EmbeddingMethod
    embedding_model: str | None = None


class TargetAttributor:
    """Isolated post-hoc target attributor used only for evaluation experiments.

    This class is intentionally separate from ``RecallMatcher``. The live detector
    continues to use ``RecallMatcher.find_best_match`` for recall-gap scoring, so an
    attribution experiment cannot change detector risk scores or alert decisions.
    """

    def __init__(
        self,
        *,
        embedder: Embedder,
        baseline_matcher: RecallMatcher | None = None,
        target_match_threshold: float = 0.45,
    ):
        self.embedder = embedder
        self.baseline_matcher = baseline_matcher or RecallMatcher()
        self.target_match_threshold = target_match_threshold

    def texts(
        self,
        cluster: ComplaintCluster,
        signatures: Sequence[FailureSignature],
        recall: Recall,
    ) -> tuple[str, str]:
        # Deliberately reuse the exact normalized texts from RecallMatcher so
        # Experiment A changes only the similarity technology, not representation.
        return (
            self.baseline_matcher._cluster_text(cluster, signatures),
            self.baseline_matcher._recall_text(recall),
        )

    @staticmethod
    def cosine(left: np.ndarray, right: np.ndarray) -> float:
        left = np.asarray(left, dtype=np.float32).reshape(-1)
        right = np.asarray(right, dtype=np.float32).reshape(-1)
        denom = float(np.linalg.norm(left) * np.linalg.norm(right))
        if denom == 0.0:
            return 0.0
        # TF-IDF's cosine feature lives in [0, 1]. Keep Experiment A in that
        # same numeric domain without adding any learned calibration.
        return float(min(1.0, max(0.0, np.dot(left, right) / denom)))

    @staticmethod
    def substitute_embedding(
        baseline: dict[str, float], embedding_similarity: float
    ) -> dict[str, float]:
        """Replace only the 10% lexical term and reapply the frozen component gate."""
        semantic = float(min(1.0, max(0.0, embedding_similarity)))
        if baseline.get("explicit_campaign_reference", 0.0) >= 1.0:
            result = dict(baseline)
            result["semantic_embedding"] = 1.0
            result["final_embedding_experiment"] = 1.0
            return result

        mechanism = float(baseline.get("failure_mechanism", 0.0))
        consequence_family = float(baseline.get("consequence_family", 0.0))
        component = float(baseline.get("component", 0.0))
        subsystem = float(baseline.get("subsystem", 0.0))
        consequence = float(baseline.get("consequence", 0.0))
        component_compatible = float(baseline.get("component_compatible", 0.0))

        final = (
            0.30 * mechanism
            + 0.20 * consequence_family
            + 0.20 * component
            + 0.10 * subsystem
            + 0.10 * consequence
            + 0.10 * semantic
        )
        if component_compatible == 0.0:
            if mechanism < 1.0:
                final *= 0.25
            else:
                final = min(final, 0.44)

        result = dict(baseline)
        result["semantic_embedding"] = round(semantic, 4)
        result["final_embedding_experiment"] = round(
            min(1.0, max(0.0, final)), 4
        )
        return result

    async def score(
        self,
        cluster: ComplaintCluster,
        signatures: Sequence[FailureSignature],
        recall: Recall,
        complaints: Sequence[Complaint] | None = None,
    ) -> TargetAttributionScore:
        baseline = self.baseline_matcher.score_target_details(
            cluster, signatures, recall, complaints
        )
        cluster_text, recall_text = self.texts(cluster, signatures, recall)
        embedded = await self.embedder.embed([cluster_text, recall_text])
        if embedded.method != EmbeddingMethod.NIM:
            raise RuntimeError(
                "Target attribution Experiment A requires NIM embeddings; "
                f"got {embedded.method.value}."
            )
        matrix = np.asarray(embedded.matrix, dtype=np.float32)
        if matrix.shape[0] != 2:
            raise RuntimeError(
                f"Target attribution embedder returned {matrix.shape[0]} vectors for 2 texts"
            )
        similarity = self.cosine(matrix[0], matrix[1])
        experimental = self.substitute_embedding(baseline, similarity)
        return TargetAttributionScore(
            baseline=baseline,
            embedding_similarity=similarity,
            experimental=experimental,
            embedding_method=embedded.method,
            embedding_model=embedded.model_name,
        )
