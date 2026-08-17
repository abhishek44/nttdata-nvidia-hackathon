from datetime import date

import pytest

from recallzero.intelligence.clustering import ComplaintClusterer
from recallzero.intelligence.embeddings import TFIDFEmbedder
from recallzero.intelligence.extractor import HeuristicFailureExtractor
from recallzero.models import Complaint, Vehicle


@pytest.mark.asyncio
async def test_semantically_similar_complaints_group() -> None:
    vehicle = Vehicle(make="Demo", model="EV", model_years=(2022,))
    complaints = [
        Complaint(odi_number="1", vehicle=vehicle, received_date=date(2022, 5, 1), components=("POWER TRAIN",), narrative="Vehicle lost motive power while driving."),
        Complaint(odi_number="2", vehicle=vehicle, received_date=date(2022, 5, 2), components=("POWER TRAIN",), narrative="The car shut off on the highway and would not accelerate."),
        Complaint(odi_number="3", vehicle=vehicle, received_date=date(2022, 5, 3), components=("POWER TRAIN",), narrative="Propulsion power disappeared while the vehicle was in motion."),
        Complaint(odi_number="4", vehicle=vehicle, received_date=date(2022, 5, 4), components=("VISIBILITY",), narrative="The sunroof glass shattered while driving."),
    ]
    extractor = HeuristicFailureExtractor()
    signatures = [await extractor.extract(item) for item in complaints]
    clusterer = ComplaintClusterer(TFIDFEmbedder(), eps=0.72, min_samples=2)
    clusters, embedding = await clusterer.cluster(complaints, signatures)
    power_clusters = [item for item in clusters if "LOSS OF MOTIVE POWER" in item.label]
    assert power_clusters
    assert power_clusters[0].evidence_count >= 2
    assert embedding.method.value == "tfidf"
