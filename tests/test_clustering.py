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


@pytest.mark.asyncio
async def test_hierarchical_clustering_keeps_brakes_and_electrical_separate() -> None:
    from recallzero.models import ExtractionMethod, FailureSignature

    vehicle = Vehicle(make="Demo", model="EV", model_years=(2022,))
    complaints = [
        Complaint(
            odi_number="b1",
            vehicle=vehicle,
            received_date=date(2022, 5, 1),
            components=("SERVICE BRAKES",),
            narrative="Parking brake fault displayed while driving.",
        ),
        Complaint(
            odi_number="b2",
            vehicle=vehicle,
            received_date=date(2022, 5, 2),
            components=("SERVICE BRAKES",),
            narrative="Parking brake malfunction warning appeared.",
        ),
        Complaint(
            odi_number="e1",
            vehicle=vehicle,
            received_date=date(2022, 5, 3),
            components=("ELECTRICAL SYSTEM",),
            narrative="Electrical system fault and display warning appeared.",
        ),
        Complaint(
            odi_number="e2",
            vehicle=vehicle,
            received_date=date(2022, 5, 4),
            components=("ELECTRICAL SYSTEM",),
            narrative="Electrical fault warning on startup.",
        ),
    ]
    # Simulate an over-general semantic extractor: all records receive the same
    # generic normalized system/mode. NHTSA component evidence should still
    # prevent cross-system merging at the first hierarchy level.
    signatures = [
        FailureSignature(
            complaint_id=item.odi_number,
            system="ELECTRICAL SYSTEM",
            failure_mode="SYSTEM MALFUNCTION",
            extraction_method=ExtractionMethod.NIM,
            model_name="test-model",
        )
        for item in complaints
    ]
    clusterer = ComplaintClusterer(TFIDFEmbedder(), eps=0.95, min_samples=2, hierarchical=True)
    clusters, _embedding, diagnostics = await clusterer.cluster_with_diagnostics(complaints, signatures)

    systems = {cluster.system for cluster in clusters}
    assert "SERVICE BRAKES" in systems
    assert "ELECTRICAL SYSTEM" in systems
    assert diagnostics["component_groups"]["SERVICE BRAKES"]["count"] == 2
    assert diagnostics["component_groups"]["ELECTRICAL SYSTEM"]["count"] == 2
    assert diagnostics["suspicious_single_cluster"] is False
