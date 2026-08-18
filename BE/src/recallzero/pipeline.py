from __future__ import annotations

from recallzero.analytics.clustering import cluster_complaints
from recallzero.intelligence.signature_extractor import SignatureExtractor
from recallzero.models import Complaint, EnrichedComplaint


def enrich_and_cluster(complaints: list[Complaint]) -> list[EnrichedComplaint]:
    extractor = SignatureExtractor()
    enriched = [EnrichedComplaint(complaint=c, signature=extractor.extract(c)) for c in complaints]
    return cluster_complaints(enriched)
