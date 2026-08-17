from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import date
from typing import Any

import numpy as np
from scipy import sparse
from sklearn.cluster import AgglomerativeClustering, DBSCAN
from sklearn.metrics.pairwise import cosine_similarity

from recallzero.intelligence.embeddings import Embedder, EmbeddingResult
from recallzero.intelligence.taxonomy import (
    CONSEQUENCE_FAMILY_LABELS,
    FAILURE_MECHANISM_LABELS,
    derive_consequence_family,
    derive_defect_family,
    derive_failure_mechanism,
)
from recallzero.models import ClusterMember, Complaint, ComplaintCluster, FailureSignature
from recallzero.utils import stable_id


FAMILY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("SERVICE BRAKES", ("SERVICE BRAKES", "PARKING BRAKE", "BRAKE")),
    ("STEERING", ("STEERING",)),
    ("POWER TRAIN", ("POWER TRAIN", "POWERTRAIN", "TRANSMISSION")),
    ("ENGINE", ("ENGINE",)),
    ("ELECTRICAL SYSTEM", ("ELECTRICAL SYSTEM", "ELECTRICAL")),
    ("AIR BAGS", ("AIR BAGS", "AIRBAG", "AIR BAG")),
    ("FUEL/PROPULSION SYSTEM", ("FUEL/PROPULSION", "FUEL SYSTEM", "PROPULSION SYSTEM")),
    ("DRIVER ASSISTANCE", ("FORWARD COLLISION AVOIDANCE", "LANE DEPARTURE", "BACK OVER PREVENTION")),
    ("VEHICLE SPEED CONTROL", ("VEHICLE SPEED CONTROL", "CRUISE CONTROL")),
    ("VISIBILITY", ("VISIBILITY", "WINDSHIELD", "WIPER")),
    ("SUSPENSION", ("SUSPENSION",)),
    ("WHEELS/TIRES", ("WHEELS", "TIRES", "TIRE")),
    ("SEAT BELTS", ("SEAT BELTS", "SEAT BELT")),
    ("STRUCTURE", ("STRUCTURE",)),
    ("EQUIPMENT", ("EQUIPMENT",)),
)

FAMILY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "SERVICE BRAKES": ("brake", "braking", "pedal", "abs", "parking brake"),
    "STEERING": ("steer", "steering", "eps", "power steering"),
    "POWER TRAIN": ("propulsion", "motive power", "accelerate", "transmission", "power train", "powertrain"),
    "ENGINE": ("engine", "motor", "stall"),
    "ELECTRICAL SYSTEM": ("electrical", "battery", "voltage", "software", "display", "12v"),
    "AIR BAGS": ("airbag", "air bag", "srs"),
    "FUEL/PROPULSION SYSTEM": ("fuel", "propulsion", "gasoline"),
    "DRIVER ASSISTANCE": ("collision", "lane", "camera", "automatic braking", "adas"),
    "VEHICLE SPEED CONTROL": ("cruise", "speed control", "accelerat"),
    "VISIBILITY": ("windshield", "glass", "visibility", "wiper"),
    "SUSPENSION": ("suspension", "strut", "control arm"),
    "WHEELS/TIRES": ("wheel", "tire", "tyre"),
    "SEAT BELTS": ("seat belt", "seatbelt", "restraint"),
    "STRUCTURE": ("door", "body", "structure", "roof"),
    "EQUIPMENT": ("equipment", "accessory"),
}


def canonical_family(value: str | None) -> str:
    text = " ".join((value or "").upper().split())
    if not text:
        return "UNKNOWN"
    for family, aliases in FAMILY_RULES:
        if any(alias in text for alias in aliases):
            return family
    return text if text != "UNKNOWN" else "UNKNOWN"


def resolve_component_family(complaint: Complaint, signature: FailureSignature) -> str:
    """Resolve stable first-level component grouping from NHTSA + normalized meaning."""

    component_families = [canonical_family(component) for component in complaint.components]
    component_families = [item for item in component_families if item != "UNKNOWN"]
    unique_components = list(dict.fromkeys(component_families))
    signature_family = canonical_family(signature.system)

    if signature_family in unique_components:
        return signature_family
    if len(unique_components) == 1:
        return unique_components[0]
    if unique_components:
        narrative = complaint.narrative.lower()
        scores: list[tuple[int, int, str]] = []
        for index, family in enumerate(unique_components):
            score = sum(narrative.count(term) for term in FAMILY_KEYWORDS.get(family, ()))
            scores.append((score, -index, family))
        best = max(scores)
        if best[0] > 0:
            return best[2]
        return unique_components[0]
    return signature_family


def build_cluster_document(
    complaint: Complaint,
    signature: FailureSignature,
    family: str | None = None,
    defect_family: str | None = None,
    failure_mechanism: str | None = None,
    consequence_family: str | None = None,
) -> str:
    family = family or resolve_component_family(complaint, signature)
    defect_family = defect_family or derive_defect_family(complaint, signature)
    failure_mechanism = failure_mechanism or derive_failure_mechanism(complaint, signature)
    consequence_family = consequence_family or derive_consequence_family(complaint, signature)
    narrative = " ".join(complaint.narrative.split())[:350]
    return (
        f"component family: {family}\n"
        f"failure mechanism: {failure_mechanism}\n"
        f"consequence family: {consequence_family}\n"
        f"legacy defect family: {defect_family}\n"
        f"failure mode: {signature.failure_mode}\n"
        f"symptom: {signature.symptom or 'unknown'}\n"
        f"operating state: {signature.operating_state or 'unknown'}\n"
        f"consequence: {signature.consequence or 'unknown'}\n"
        f"owner narrative context: {narrative}"
    )


class ComplaintClusterer:
    def __init__(
        self,
        embedder: Embedder,
        eps: float = 0.27,
        min_samples: int = 2,
        max_representatives: int = 3,
        hierarchical: bool = True,
        diagnostics_sample_size: int = 160,
        suspicious_cluster_share: float = 0.70,
        taxonomy_grouping: bool = True,
        refine_max_distance: float = 0.34,
    ):
        self.embedder = embedder
        self.eps = eps
        self.min_samples = min_samples
        self.max_representatives = max_representatives
        self.hierarchical = hierarchical
        self.diagnostics_sample_size = diagnostics_sample_size
        self.suspicious_cluster_share = suspicious_cluster_share
        self.taxonomy_grouping = taxonomy_grouping
        self.refine_max_distance = refine_max_distance

    @staticmethod
    def _mode(values: Sequence[str], default: str) -> str:
        cleaned = [value for value in values if value]
        return Counter(cleaned).most_common(1)[0][0] if cleaned else default

    def _representatives(
        self,
        matrix: np.ndarray | sparse.spmatrix,
        indices: list[int],
    ) -> tuple[list[int], dict[int, float]]:
        if len(indices) == 1:
            return indices, {indices[0]: 1.0}
        sub = matrix[indices]
        centroid = sub.mean(axis=0)
        centroid_matrix = centroid if sparse.issparse(centroid) else np.asarray(centroid).reshape(1, -1)
        scores = cosine_similarity(sub, centroid_matrix).ravel()
        ranked_local = list(np.argsort(-scores))
        representative_indices = [indices[local] for local in ranked_local[: self.max_representatives]]
        score_map = {indices[local]: max(-1.0, min(1.0, float(scores[local]))) for local in range(len(indices))}
        return representative_indices, score_map

    def _distance_summary(self, matrix: np.ndarray | sparse.spmatrix, indices: list[int]) -> dict[str, float] | None:
        if len(indices) < 2:
            return None
        if len(indices) > self.diagnostics_sample_size:
            positions = np.linspace(0, len(indices) - 1, self.diagnostics_sample_size, dtype=int)
            sample_indices = [indices[int(pos)] for pos in positions]
        else:
            sample_indices = indices
        similarities = cosine_similarity(matrix[sample_indices])
        distances = 1.0 - similarities
        upper = distances[np.triu_indices_from(distances, k=1)]
        if not upper.size:
            return None
        return {
            "min": round(float(np.min(upper)), 4),
            "median": round(float(np.median(upper)), 4),
            "p90": round(float(np.quantile(upper, 0.90)), 4),
            "max": round(float(np.max(upper)), 4),
        }

    def _refine_cluster(
        self,
        matrix: np.ndarray | sparse.spmatrix,
        indices: list[int],
    ) -> list[list[int]]:
        """Break DBSCAN density chains with complete-link distance refinement."""
        if len(indices) < 3:
            return [indices]
        summary = self._distance_summary(matrix, indices)
        if summary is None or summary["max"] <= self.refine_max_distance:
            return [indices]
        sub = matrix[indices]
        dense = sub.toarray() if sparse.issparse(sub) else np.asarray(sub)
        labels = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=self.refine_max_distance,
            metric="cosine",
            linkage="complete",
        ).fit_predict(dense)
        groups: dict[int, list[int]] = {}
        for local_index, label in enumerate(labels.tolist()):
            groups.setdefault(int(label), []).append(indices[local_index])
        return list(groups.values())

    async def cluster_with_diagnostics(
        self,
        complaints: Sequence[Complaint],
        signatures: Sequence[FailureSignature],
    ) -> tuple[list[ComplaintCluster], EmbeddingResult, dict[str, Any]]:
        if len(complaints) != len(signatures):
            raise ValueError("Complaints and signatures must have the same length")
        if not complaints:
            embedding = await self.embedder.embed([])
            return [], embedding, {
                "algorithm": "hierarchical_dual_axis_dbscan_complete_link_max_guard",
                "eps": self.eps,
                "min_samples": self.min_samples,
                "refine_max_distance": self.refine_max_distance,
                "component_groups": {},
                "cluster_sizes": [],
                "noise_count": 0,
                "suspicious_single_cluster": False,
            }

        complaint_by_id = {complaint.odi_number: complaint for complaint in complaints}
        signatures_by_id = {signature.complaint_id: signature for signature in signatures}
        ordered_ids = [complaint.odi_number for complaint in complaints if complaint.odi_number in signatures_by_id]
        families = [resolve_component_family(complaint_by_id[item_id], signatures_by_id[item_id]) for item_id in ordered_ids]
        defect_families = [
            derive_defect_family(complaint_by_id[item_id], signatures_by_id[item_id]) for item_id in ordered_ids
        ]
        failure_mechanisms = [
            derive_failure_mechanism(complaint_by_id[item_id], signatures_by_id[item_id]) for item_id in ordered_ids
        ]
        consequence_families = [
            derive_consequence_family(complaint_by_id[item_id], signatures_by_id[item_id]) for item_id in ordered_ids
        ]
        documents = [
            build_cluster_document(
                complaint_by_id[item_id],
                signatures_by_id[item_id],
                families[index],
                defect_families[index],
                failure_mechanisms[index],
                consequence_families[index],
            )
            for index, item_id in enumerate(ordered_ids)
        ]
        embedding = await self.embedder.embed(documents)

        hierarchy_groups: dict[tuple[str, str], list[int]] = {}
        if self.hierarchical:
            for index, family in enumerate(families):
                # Child clusters are consequence-oriented. Root/mechanism identity is
                # carried independently and is later used for cross-component meta-signals.
                taxonomy = consequence_families[index] if self.taxonomy_grouping else "ALL"
                hierarchy_groups.setdefault((family, taxonomy), []).append(index)
        else:
            hierarchy_groups[("ALL", "ALL")] = list(range(len(ordered_ids)))

        groups: dict[str, list[int]] = {}
        component_indices: dict[str, list[int]] = {}
        component_taxonomy: dict[str, dict[str, list[int]]] = {}
        component_cluster_count: Counter[str] = Counter()
        component_noise_count: Counter[str] = Counter()
        noise_count = 0

        for (family, consequence_family), indices in sorted(hierarchy_groups.items()):
            component_indices.setdefault(family, []).extend(indices)
            component_taxonomy.setdefault(family, {})[consequence_family] = list(indices)
            if len(indices) < self.min_samples:
                labels = np.full(len(indices), -1, dtype=int)
            else:
                labels = DBSCAN(eps=self.eps, min_samples=self.min_samples, metric="cosine").fit_predict(
                    embedding.matrix[indices]
                )

            raw_groups: dict[int, list[int]] = {}
            for local_index, raw_label in enumerate(labels.tolist()):
                raw_groups.setdefault(int(raw_label), []).append(indices[local_index])

            for raw_label, raw_indices in raw_groups.items():
                if raw_label == -1:
                    for global_index in raw_indices:
                        key = f"{family}|{consequence_family}|noise_{global_index}"
                        groups[key] = [global_index]
                        noise_count += 1
                        component_noise_count[family] += 1
                    continue

                refined = self._refine_cluster(embedding.matrix, raw_indices)
                for refinement_index, refined_indices in enumerate(refined):
                    key = f"{family}|{consequence_family}|cluster_{raw_label}_{refinement_index}"
                    groups[key] = refined_indices
                    component_cluster_count[family] += 1

        component_diagnostics: dict[str, Any] = {}
        for family, indices in sorted(component_indices.items()):
            taxonomy_rows: dict[str, Any] = {}
            for consequence_family, family_indices in sorted(component_taxonomy[family].items()):
                output_clusters = [
                    values
                    for key, values in groups.items()
                    if key.startswith(f"{family}|{consequence_family}|") and "|noise_" not in key
                ]
                output_noise = sum(
                    len(values)
                    for key, values in groups.items()
                    if key.startswith(f"{family}|{consequence_family}|noise_")
                )
                taxonomy_rows[consequence_family] = {
                    "count": len(family_indices),
                    "clusters": len(output_clusters),
                    "noise_points": output_noise,
                    "cosine_distance": self._distance_summary(embedding.matrix, family_indices),
                }
            component_diagnostics[family] = {
                "count": len(indices),
                "dbscan_clusters": component_cluster_count[family],
                "noise_points": component_noise_count[family],
                "cosine_distance": self._distance_summary(embedding.matrix, indices),
                "consequence_family_groups": taxonomy_rows,
                "defect_family_groups": taxonomy_rows,
            }

        clusters: list[ComplaintCluster] = []
        purity_rows: list[dict[str, Any]] = []
        for key, indices in groups.items():
            member_ids = [ordered_ids[index] for index in indices]
            member_signatures = [signatures_by_id[item_id] for item_id in member_ids]
            member_complaints = [complaint_by_id[item_id] for item_id in member_ids]
            parts = key.split("|", 2)
            family = parts[0]
            consequence_family = parts[1]
            raw_failure_mode = self._mode([item.failure_mode for item in member_signatures], "UNSPECIFIED FAILURE")
            member_mechanisms = [
                derive_failure_mechanism(complaint_by_id[item_id], signatures_by_id[item_id]) for item_id in member_ids
            ]
            failure_mechanism = self._mode(member_mechanisms, "OTHER")
            member_legacy_families = [
                derive_defect_family(complaint_by_id[item_id], signatures_by_id[item_id]) for item_id in member_ids
            ]
            defect_family = self._mode(member_legacy_families, consequence_family)
            representatives, score_map = self._representatives(embedding.matrix, indices)
            representative_ids = tuple(ordered_ids[index] for index in representatives)
            stable_member_ids = tuple(sorted(member_ids))
            cluster_id = stable_id("cl", family, consequence_family, failure_mechanism, raw_failure_mode, *stable_member_ids)
            stable_label = CONSEQUENCE_FAMILY_LABELS.get(consequence_family, consequence_family.replace("_", " ").title())
            label = f"{family} — {raw_failure_mode}"
            dates = [item.received_date for item in member_complaints]
            is_noise = "|noise_" in key
            clusters.append(
                ComplaintCluster(
                    cluster_id=cluster_id,
                    label=label,
                    system=family,
                    failure_mode=raw_failure_mode,
                    defect_family=defect_family,
                    failure_mechanism=failure_mechanism,
                    consequence_family=consequence_family,
                    member_ids=stable_member_ids,
                    source_systems=(family,),
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
                    is_noise=is_noise,
                    embedding_method=embedding.method,
                )
            )
            raw_mode_count = Counter(item.failure_mode for item in member_signatures).most_common(1)[0][1]
            derived_families = [
                derive_defect_family(complaint_by_id[item_id], signatures_by_id[item_id]) for item_id in member_ids
            ]
            defect_mode_count = Counter(derived_families).most_common(1)[0][1]
            mechanism_mode_count = Counter(member_mechanisms).most_common(1)[0][1]
            derived_consequences = [
                derive_consequence_family(complaint_by_id[item_id], signatures_by_id[item_id]) for item_id in member_ids
            ]
            consequence_mode_count = Counter(derived_consequences).most_common(1)[0][1]
            purity_rows.append(
                {
                    "cluster_id": cluster_id,
                    "label": label,
                    "canonical_defect_label": stable_label,
                    "size": len(indices),
                    "defect_family": defect_family,
                    "failure_mechanism": failure_mechanism,
                    "failure_mechanism_label": FAILURE_MECHANISM_LABELS.get(failure_mechanism, failure_mechanism),
                    "consequence_family": consequence_family,
                    "failure_mode_purity": round(raw_mode_count / max(1, len(indices)), 3),
                    "raw_failure_mode_purity": round(raw_mode_count / max(1, len(indices)), 3),
                    "defect_family_purity": round(defect_mode_count / max(1, len(indices)), 3),
                    "failure_mechanism_purity": round(mechanism_mode_count / max(1, len(indices)), 3),
                    "consequence_family_purity": round(consequence_mode_count / max(1, len(indices)), 3),
                    "cosine_distance": self._distance_summary(embedding.matrix, indices),
                    "noise": is_noise,
                }
            )

        clusters.sort(key=lambda item: (-item.evidence_count, item.label, item.cluster_id))
        cluster_sizes = [item.evidence_count for item in clusters]
        largest_share = max(cluster_sizes, default=0) / max(1, len(ordered_ids))
        diagnostics = {
            "algorithm": "hierarchical_dual_axis_dbscan_complete_link_max_guard" if self.hierarchical else "dbscan_complete_link_max_guard",
            "eps": self.eps,
            "min_samples": self.min_samples,
            "taxonomy_grouping": self.taxonomy_grouping,
            "refine_max_distance": self.refine_max_distance,
            "component_groups": component_diagnostics,
            "cluster_sizes": cluster_sizes,
            "noise_count": noise_count,
            "largest_cluster_share": round(largest_share, 4),
            "suspicious_single_cluster": len(ordered_ids) >= 10 and len(clusters) <= 1,
            "suspicious_dominant_cluster": len(ordered_ids) >= 20 and largest_share >= self.suspicious_cluster_share,
            "cluster_purity": purity_rows[:40],
        }
        return clusters, embedding, diagnostics

    async def cluster(
        self,
        complaints: Sequence[Complaint],
        signatures: Sequence[FailureSignature],
    ) -> tuple[list[ComplaintCluster], EmbeddingResult]:
        clusters, embedding, _ = await self.cluster_with_diagnostics(complaints, signatures)
        return clusters, embedding
