from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from recallzero.backtest.time_machine import run_time_machine
from recallzero.clients.nhtsa import NHTSAClient
from recallzero.models import EnrichedComplaint, VehicleKey
from recallzero.pipeline import enrich_and_cluster


def _posthoc_match_cluster(clusters: dict[str, list[EnrichedComplaint]], recall_problem: str) -> tuple[str, float]:
    """Match detected clusters to the known recall only AFTER detection.

    This is evaluation logic, not signal-generation logic. The recall text never influences
    extraction, clustering, trend scoring, or alert threshold crossings.
    """
    ids = [cid for cid in clusters if cid != "noise"]
    if not ids:
        raise ValueError("No non-noise complaint clusters were produced")
    docs = []
    for cid in ids:
        rows = clusters[cid]
        docs.append(" ".join([rows[0].signature.canonical_text()] + [r.complaint.summary for r in rows]))
    corpus = [recall_problem] + docs
    matrix = TfidfVectorizer(stop_words="english", ngram_range=(1, 2)).fit_transform(corpus)
    sims = cosine_similarity(matrix[0:1], matrix[1:]).ravel()
    best = int(sims.argmax())
    return ids[best], float(sims[best])


def run_candidate(candidate_path: str | Path, client: NHTSAClient | None = None) -> dict:
    cfg = json.loads(Path(candidate_path).read_text(encoding="utf-8"))
    recall_date = date.fromisoformat(cfg["recall_date"])
    client = client or NHTSAClient()

    complaints = []
    for v in cfg["vehicles"]:
        rows = client.complaints_by_vehicle(VehicleKey(**v))
        complaints.extend(c for c in rows if c.date_complaint_filed < recall_date)

    # Critical anti-leakage rule: the recall description is not passed into this pipeline.
    enriched = enrich_and_cluster(complaints)
    clusters: dict[str, list[EnrichedComplaint]] = {}
    for item in enriched:
        clusters.setdefault(item.cluster_id or "unclustered", []).append(item)

    detected = {}
    for cid, rows in clusters.items():
        if cid == "noise":
            continue
        detected[cid] = run_time_machine(
            rows,
            campaign_number=cfg["campaign_number"],
            recall_date=recall_date,
            alert_threshold=75.0,
            min_reports=4,
        )

    matched_cluster, eval_similarity = _posthoc_match_cluster(clusters, cfg["problem"])
    matched_result = detected.get(matched_cluster)
    return {
        "campaign_number": cfg["campaign_number"],
        "recall_date": recall_date.isoformat(),
        "pre_recall_complaints": len(complaints),
        "clusters_detected": len([c for c in clusters if c != "noise"]),
        "evaluation_match": {
            "cluster_id": matched_cluster,
            "similarity": round(eval_similarity, 4),
            "note": "Recall text is used only post-hoc to score which independently detected cluster corresponds to the historical recall.",
        },
        "matched_cluster_backtest": matched_result.model_dump(mode="json") if matched_result else None,
        "all_cluster_backtests": {cid: result.model_dump(mode="json") for cid, result in detected.items()},
    }
