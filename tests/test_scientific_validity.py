from datetime import date

import pytest

from recallzero.analytics import SeverityEngine
from recallzero.backtest import RecallTimeMachine
from recallzero.config import Settings
from recallzero.demo import build_demo_records
from recallzero.intelligence.clustering import ComplaintClusterer
from recallzero.intelligence.embeddings import TFIDFEmbedder
from recallzero.intelligence.taxonomy import derive_defect_family
from recallzero.models import (
    Complaint,
    ComplaintCluster,
    EmbeddingMethod,
    ExtractionMethod,
    FailureSignature,
    Recall,
    Vehicle,
)
from recallzero.pipeline import build_pipeline
from recallzero.recall import RecallMatcher


def _vehicle() -> Vehicle:
    return Vehicle(make="DEMO", model="EV", model_years=(2022,))


def _sig(complaint_id: str, *, system: str, failure_mode: str, indicators=(), subsystem=None, state=None):
    return FailureSignature(
        complaint_id=complaint_id,
        system=system,
        subsystem=subsystem,
        failure_mode=failure_mode,
        operating_state=state,
        severity_indicators=indicators,
        extraction_method=ExtractionMethod.NIM,
        model_name="test-model",
    )


def test_severity_rejects_hypothetical_crash_and_parked_motion() -> None:
    complaint = Complaint(
        odi_number="1",
        vehicle=_vehicle(),
        received_date=date(2022, 5, 1),
        components=("FORWARD COLLISION AVOIDANCE",),
        narrative="Front camera fault. This could cause a crash or injury. Vehicle was parked when inspected.",
        crash=False,
        injuries=0,
    )
    signature = _sig(
        "1",
        system="DRIVER ASSISTANCE",
        failure_mode="CAMERA FAULT",
        indicators=("crash_reported", "injury_reported", "vehicle_in_motion"),
        state="PARKED",
    )
    result = SeverityEngine().calculate([complaint], [signature])
    assert "crash_reported" not in result.indicator_counts
    assert "injury_reported" not in result.indicator_counts
    assert "vehicle_in_motion" not in result.indicator_counts
    assert result.rejected_indicator_counts["crash_reported"] == 1


def test_severity_uses_structured_source_flags() -> None:
    complaint = Complaint(
        odi_number="2",
        vehicle=_vehicle(),
        received_date=date(2022, 5, 2),
        components=("SERVICE BRAKES",),
        narrative="Brake pedal was depressed and the vehicle failed to stop while driving at 30 mph.",
        crash=True,
        injuries=1,
    )
    signature = _sig("2", system="SERVICE BRAKES", failure_mode="BRAKE FAILURE", indicators=())
    result = SeverityEngine().calculate([complaint], [signature])
    assert result.indicator_counts["crash_reported"] == 1
    assert result.indicator_counts["injury_reported"] == 1
    assert result.indicator_counts["loss_of_braking"] == 1
    assert result.indicator_counts["vehicle_in_motion"] == 1


def test_taxonomy_splits_hvbjb_from_phone_key() -> None:
    hv = Complaint(
        odi_number="h1",
        vehicle=_vehicle(),
        received_date=date(2022, 5, 1),
        components=("ELECTRICAL SYSTEM",),
        narrative="Stop Safely Now. Dealer replaced the High Voltage Battery Junction Box after the car would not move.",
    )
    key = Complaint(
        odi_number="k1",
        vehicle=_vehicle(),
        received_date=date(2022, 5, 2),
        components=("ELECTRICAL SYSTEM",),
        narrative="Phone As A Key stopped recognizing the phone and the owner could not unlock the vehicle.",
    )
    hv_sig = _sig("h1", system="ELECTRICAL SYSTEM", failure_mode="FAILURE TO START", subsystem="HVBJB")
    key_sig = _sig("k1", system="ELECTRICAL SYSTEM", failure_mode="INTERMITTENT PHONE RECOGNITION", subsystem="PAAK")
    assert derive_defect_family(hv, hv_sig) == "HIGH_VOLTAGE_POWER_DISTRIBUTION"
    assert derive_defect_family(key, key_sig) == "ACCESS_OR_KEY_FAILURE"


@pytest.mark.asyncio
async def test_taxonomy_layer_prevents_same_component_density_chaining() -> None:
    vehicle = _vehicle()
    complaints = [
        Complaint(
            odi_number="h1",
            vehicle=vehicle,
            received_date=date(2022, 5, 1),
            components=("ELECTRICAL SYSTEM",),
            narrative="High Voltage Battery Junction Box failure. Stop Safely Now and vehicle would not move.",
        ),
        Complaint(
            odi_number="h2",
            vehicle=vehicle,
            received_date=date(2022, 5, 2),
            components=("ELECTRICAL SYSTEM",),
            narrative="HVBJB contactor failure left the vehicle unable to drive.",
        ),
        Complaint(
            odi_number="k1",
            vehicle=vehicle,
            received_date=date(2022, 5, 3),
            components=("ELECTRICAL SYSTEM",),
            narrative="Phone As A Key stopped recognizing the phone.",
        ),
        Complaint(
            odi_number="k2",
            vehicle=vehicle,
            received_date=date(2022, 5, 4),
            components=("ELECTRICAL SYSTEM",),
            narrative="PAAK key app failed and the owner could not unlock the car.",
        ),
    ]
    signatures = [
        _sig("h1", system="ELECTRICAL SYSTEM", failure_mode="FAILURE", subsystem="HVBJB"),
        _sig("h2", system="ELECTRICAL SYSTEM", failure_mode="FAILURE", subsystem="CONTACTOR"),
        _sig("k1", system="ELECTRICAL SYSTEM", failure_mode="FAILURE", subsystem="PAAK"),
        _sig("k2", system="ELECTRICAL SYSTEM", failure_mode="FAILURE", subsystem="PHONE KEY"),
    ]
    clusterer = ComplaintClusterer(TFIDFEmbedder(), eps=0.99, min_samples=2, taxonomy_grouping=True)
    clusters, _embedding, diagnostics = await clusterer.cluster_with_diagnostics(complaints, signatures)
    non_noise = [item for item in clusters if not item.is_noise]
    assert {item.defect_family for item in non_noise} == {
        "HIGH_VOLTAGE_POWER_DISTRIBUTION",
        "ACCESS_OR_KEY_FAILURE",
    }
    assert diagnostics["component_groups"]["ELECTRICAL SYSTEM"]["dbscan_clusters"] == 2


def test_recall_matcher_uses_explicit_campaign_reference_as_sanity_check() -> None:
    vehicle = _vehicle()
    complaint = Complaint(
        odi_number="v1",
        vehicle=vehicle,
        received_date=date(2022, 5, 1),
        components=("VISIBILITY",),
        narrative="NHTSA Campaign Number 21V711000 recall repair parts are still not available.",
    )
    signature = _sig("v1", system="VISIBILITY", failure_mode="RECALL REPAIR DELAY")
    cluster = ComplaintCluster(
        cluster_id="c1",
        label="VISIBILITY — RECALL REPAIR DELAY",
        system="VISIBILITY",
        failure_mode="RECALL REPAIR DELAY",
        defect_family="RECALL_SERVICE_ISSUE",
        member_ids=("v1",),
        first_received_date=date(2022, 5, 1),
        last_received_date=date(2022, 5, 1),
        is_noise=False,
        embedding_method=EmbeddingMethod.TFIDF,
    )
    recall = Recall(
        campaign_number="21V711000",
        vehicle=vehicle,
        report_received_date=date(2021, 9, 1),
        component="VISIBILITY: WINDSHIELD",
        summary="The windshield may not be properly bonded to the vehicle.",
        consequence="The windshield may detach in a crash.",
    )
    details = RecallMatcher().score_target_details(cluster, [signature], recall, [complaint])
    assert details["explicit_campaign_reference"] == 1.0
    assert details["final"] == 1.0


def test_recall_matcher_structurally_matches_hv_power_loss() -> None:
    vehicle = _vehicle()
    complaint = Complaint(
        odi_number="e1",
        vehicle=vehicle,
        received_date=date(2022, 4, 25),
        components=("ELECTRICAL SYSTEM", "POWER TRAIN"),
        narrative="While driving at highway speed the vehicle lost motive power. Dealer diagnosed a high voltage battery junction box failure.",
    )
    signature = _sig(
        "e1",
        system="ELECTRICAL SYSTEM",
        subsystem="HIGH VOLTAGE BATTERY JUNCTION BOX",
        failure_mode="LOSS OF MOTIVE POWER",
        state="VEHICLE IN MOTION",
    )
    cluster = ComplaintCluster(
        cluster_id="c2",
        label="ELECTRICAL SYSTEM — LOSS OF MOTIVE POWER",
        system="ELECTRICAL SYSTEM",
        failure_mode="LOSS OF MOTIVE POWER",
        defect_family="LOSS_OF_MOTIVE_POWER",
        member_ids=("e1",),
        first_received_date=date(2022, 4, 25),
        last_received_date=date(2022, 4, 25),
        is_noise=False,
        embedding_method=EmbeddingMethod.TFIDF,
    )
    recall = Recall(
        campaign_number="22V412000",
        vehicle=vehicle,
        report_received_date=date(2022, 6, 10),
        component="ELECTRICAL SYSTEM: PROPULSION",
        summary="High-voltage battery main contactors may overheat and open, causing a loss of motive power.",
        consequence="A loss of motive power can increase the risk of a crash.",
    )
    details = RecallMatcher().score_target_details(cluster, [signature], recall, [complaint])
    assert details["defect_family"] == 1.0
    assert details["final"] >= 0.45


@pytest.mark.asyncio
async def test_time_machine_distinguishes_unmatched_early_alert(tmp_path) -> None:
    vehicle, complaints, recalls, target, official_date = build_demo_records()
    pipeline = build_pipeline(Settings(data_dir=tmp_path, use_nim=False), use_nim=False)
    result = await RecallTimeMachine(pipeline, target_match_threshold=0.99).run(
        vehicle=vehicle,
        complaints=complaints,
        recalls=recalls,
        target_recall=target,
        official_recall_date=official_date,
        replay_start_date=date(2022, 3, 1),
        alert_threshold=30,
        minimum_evidence=2,
        save=False,
    )
    assert result.status == "EARLY_ALERT_TARGET_UNMATCHED"
    assert result.first_any_alert_date is not None
    assert result.first_matching_alert_date is None
    assert result.lead_time_days is None
