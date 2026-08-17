from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import date

import numpy as np
from scipy import sparse
from sklearn.cluster import DBSCAN
from sklearn.metrics.pairwise import cosine_similarity

from recallzero.intelligence.embeddings import Embedder, EmbeddingResult
from recallzero.models import ClusterMember, Complaint, ComplaintCluster, FailureSignature
from recallzero.utils import stable_id


def build_cluster_document(complaint: Complaint, signature: FailureSignature) -> str:
    """Build a meaning-first document for semantic grouping.

    The normalized signature is repeated intentionally so that long owner narratives do
    not dominate cosine distance. The raw narrative is retained as secondary context.
    """
    canonical = signature.canonical_text()
    narrative = " ".join(complaint.narrative.split())[:500]
    components = ", ".join(complaint.components) or "unknown"
    return (
        f"normalized failure signature:\n{canonical}\n"
        f"normalized failure signature repeated:\n{canonical}\n"
        f"nhtsa components: {components}\n"
        f"complaint narrative context: {narrative}"
    )


class ComplaintClusterer:
    def __init__(self, embedder: Embedder, eps: float = 0.34, min_samples: int = 2, max_representatives: int = 3):
        self.embedder = embedder
        self.eps = eps
        self.min_samples = min_samples
        self.max_representatives = max_representatives

    @staticmethod
    def _mode(values: Sequence[str], default: str) -> str:
        cleaned = [value for value in values if value]
        if not cleaned:
            return default
        return Counter(cleaned).most_common(1)[0][0]

    @staticmethod
    def _row(matrix: np.ndarray | sparse.spmatrix, index: int) -> np.ndarray | sparse.spmatrix:
        return matrix[index : index + 1]

    def _representatives(
        self,
        matrix: np.ndarray | sparse.spmatrix,
        indices: list[int],
    ) -> tuple[list[int], dict[int, float]]:
        if len(indices) == 1:
            return indices, {indices[0]: 1.0}
        sub = matrix[indices]
        centroid = sub.mean(axis=0)
        if sparse.issparse(centroid):
            centroid_matrix = centroid
        else:
            centroid_matrix = np.asarray(centroid).reshape(1, -1)
        scores = cosine_similarity(sub, centroid_matrix).ravel()
        ranked_local = list(np.argsort(-scores))
        representative_indices = [indices[local] for local in ranked_local[: self.max_representatives]]
        score_map = {indices[local]: max(-1.0, min(1.0, float(scores[local]))) for local in range(len(indices))}
        return representative_indices, score_map

    async def cluster(
        self,
        complaints: Sequence[Complaint],
        signatures: Sequence[FailureSignature],
    ) -> tuple[list[ComplaintCluster], EmbeddingResult]:
        if len(complaints) != len(signatures):
            raise ValueError("Complaints and signatures must have the same length")
        if not complaints:
            embedding = await self.embedder.embed([])
            return [], embedding

        complaint_by_id = {complaint.odi_number: complaint for complaint in complaints}
        signatures_by_id = {signature.complaint_id: signature for signature in signatures}
        ordered_ids = [complaint.odi_number for complaint in complaints if complaint.odi_number in signatures_by_id]
        documents = [build_cluster_document(complaint_by_id[item_id], signatures_by_id[item_id]) for item_id in ordered_ids]
        embedding = await self.embedder.embed(documents)

        if len(ordered_ids) == 1:
            raw_labels = np.array([-1], dtype=int)
        else:
            raw_labels = DBSCAN(eps=self.eps, min_samples=self.min_samples, metric="cosine").fit_predict(embedding.matrix)

        groups: dict[str, list[int]] = {}
        for index, raw_label in enumerate(raw_labels.tolist()):
            key = f"noise_{index}" if raw_label == -1 else f"cluster_{raw_label}"
            groups.setdefault(key, []).append(index)

        clusters: list[ComplaintCluster] = []
        for key, indices in groups.items():
            member_ids = [ordered_ids[index] for index in indices]
            member_signatures = [signatures_by_id[item_id] for item_id in member_ids]
            member_complaints = [complaint_by_id[item_id] for item_id in member_ids]
            system = self._mode([item.system for item in member_signatures], "UNKNOWN")
            failure_mode = self._mode([item.failure_mode for item in member_signatures], "UNSPECIFIED FAILURE")
            representatives, score_map = self._representatives(embedding.matrix, indices)
            representative_ids = tuple(ordered_ids[index] for index in representatives)
            stable_member_ids = tuple(sorted(member_ids))
            cluster_id = stable_id("cl", system, failure_mode, *stable_member_ids)
            label = f"{system} — {failure_mode}"
            dates = [item.received_date for item in member_complaints]
            clusters.append(
                ComplaintCluster(
                    cluster_id=cluster_id,
                    label=label,
                    system=system,
                    failure_mode=failure_mode,
                    member_ids=stable_member_ids,
                    members=tuple(
                        ClusterMember(
                            complaint_id=ordered_ids[index],
                            similarity_to_representative=score_map.get(index),
                        )
                        for index in indices
                    ),
                    representative_complaint_ids=representative_ids,
                    first_received_date=min(dates, default=date.today()),
                    last_received_date=max(dates, default=date.today()),
                    is_noise=key.startswith("noise_"),
                    embedding_method=embedding.method,
                )
            )

        clusters.sort(key=lambda item: (-item.evidence_count, item.label, item.cluster_id))
        return clusters, embedding
