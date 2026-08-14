from __future__ import annotations

import numpy as np
from sklearn.cluster import DBSCAN
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from recallzero.clients.nvidia_nim import NvidiaNIMClient
from recallzero.models import EnrichedComplaint


def cluster_complaints(items: list[EnrichedComplaint], eps: float = 0.34, min_samples: int = 3, nim: NvidiaNIMClient | None = None) -> list[EnrichedComplaint]:
    """Assign semantic cluster IDs. NIM embeddings when configured; TF-IDF fallback for local development."""
    if not items:
        return items
    texts = [f"{x.signature.canonical_text()} | {x.complaint.summary}" for x in items]
    nim = nim or NvidiaNIMClient()
    if nim.embeddings_enabled:
        matrix = np.asarray(nim.embeddings(texts), dtype=float)
        matrix = normalize(matrix)
    else:
        matrix = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), min_df=1).fit_transform(texts)
    labels = DBSCAN(eps=eps, min_samples=min_samples, metric="cosine").fit_predict(matrix)
    for item, label in zip(items, labels):
        item.cluster_id = "noise" if int(label) == -1 else f"cluster-{int(label):03d}"
    return items
