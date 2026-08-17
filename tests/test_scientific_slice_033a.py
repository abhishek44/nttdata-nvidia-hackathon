from datetime import date

import pytest

from recallzero.analytics import SeverityEngine
from recallzero.backtest import RecallTimeMachine
from recallzero.config import Settings
from recallzero.demo import build_demo_records
from recallzero.intelligence.taxonomy import derive_failure_mechanism
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
from recallzero.recall.matcher import RecallMatcher


def _vehicle() -> Vehicle:
    return Vehicle(make="FORD", model="MUSTANG MACH-E", model_years=(2021, 2022))


def _complaint(odi: str, narrative: str, components: tuple[str, ...] = ("ELECTRICAL SYSTEM",)) -> Complaint:
    return Complaint(
        odi_number=odi,
        vehicle=_vehicle(),
        received_date=date(2022, 5, 25),
        components=components,
        narrative=narrative,
    )


def _signature(
    odi: str,
    *,
    system: str = "ELECTRICAL SYSTEM",
    subsystem: str | None = "HIGH VOLTAGE BATTERY JUNCTION BOX",
    mode: str = "LOSS OF MOTIVE POWER",
    state: str | None = "VEHICLE IN MOTION",
    indicators: tuple[str, ...] = ("loss_of_motive_power", "vehicle_in_motion"),
) -> FailureSignature:
    return FailureSignature(
        complaint_id=odi,
        system=system,
        subsystem=subsystem,
        failure_mode=mode,
        operating_state=state,
        severity_indicators=indicators,
        extraction_method=ExtractionMethod.NIM,
        model_name="test-model",
    )


@pytest.mark.parametrize(
    ("odi", "narrative"),
    [
        (
            "11415152",
            "As I was driving down the road at 30 miles per hour, warning lights appeared and then the car went dead. I pulled off the road.",
        ),
        (
            "11460408",
            "While entering the freeway at about 50 mph, Stop Safely Now flashed and the throttle went dead. Speed dropped steadily.",
        ),
        (
            "11462003",
            "While driving around a curve, my car lost all power and went completely dark.",
        ),
        (
            "11465461",
            "Travelling from WI to NC, I got a Stop Safely Now alert and had to immediately pull over. Could not move vehicle in any way after this.",
        ),
        (
            "11465548",
            "I started driving out of a parking space after shopping. Emergency bells went off, alerts appeared, and the vehicle died in the travel lane.",
        ),
        (
            "11466150",
            "Driving our BEV Mach E at a normal speed, we got a Stop Safely Now error. The car turned itself off and we had to coast to the side of the road.",
        ),
    ],
)
def test_named_mach_e_moving_failures_validate_loss_of_motive_power(odi: str, narrative: str) -> None:
    complaint = _complaint(odi, narrative)
    evidence = SeverityEngine().validate_indicators(complaint, _signature(odi))
    assert "loss_of_motive_power" in evidence
    assert "vehicle_in_motion" in evidence


@pytest.mark.parametrize(
    ("odi", "narrative"),
    [
        (
            "11459506",
            'Car was parked in my home garage and would not start, displaying "Stop safely now."',
        ),
        (
            "11463755",
            "I was stranded in a parking lot in the evening when my car wouldn't start and had to call a tow truck.",
        ),
        (
            "11464507",
            'Vehicle would not start after a brief stop, with a "Stop Safely Now" error and could not be shifted out of Park.',
        ),
        (
            "11464559",
            "Upon returning to my vehicle at the end of a work day, it would not shift into drive and had to be towed.",
        ),
    ],
)
def test_named_mach_e_parked_failures_do_not_become_moving_power_loss(odi: str, narrative: str) -> None:
    complaint = _complaint(odi, narrative)
    signature = _signature(odi, state="PARKED")
    evidence = SeverityEngine().validate_indicators(complaint, signature)
    assert "loss_of_motive_power" not in evidence
    assert "vehicle_in_motion" not in evidence


def test_taxonomy_consistency_guard_downgrades_brake_mechanism_for_no_drive_powertrain_event() -> None:
    complaint = _complaint(
        "gear-1",
        "The parking brake warning appeared and the vehicle could not shift into Drive. Gear selection failed.",
        components=("POWER TRAIN",),
    )
    signature = _signature(
        "gear-1",
        system="POWER TRAIN",
        subsystem="SHIFT CONTROL",
        mode="GEAR SELECTION FAILURE",
        state="PARKED",
        indicators=(),
    )
    assert derive_failure_mechanism(complaint, signature) == "GENERAL_POWERTRAIN"


def test_consequence_distribution_contributes_to_meta_recall_matching() -> None:
    complaints = [
        _complaint(
            "m1",
            "High Voltage Battery Junction Box contactor opened while driving and the vehicle lost motive power.",
            components=("ELECTRICAL SYSTEM", "POWER TRAIN"),
        ),
        _complaint(
            "m2",
            "HVBJB failure caused Stop Safely Now and the vehicle would not start or shift into drive.",
            components=("ELECTRICAL SYSTEM",),
        ),
        _complaint(
            "m3",
            "High voltage battery junction box warning required service.",
            components=("ELECTRICAL SYSTEM",),
        ),
    ]
    signatures = [
        _signature("m1", mode="LOSS OF MOTIVE POWER", state="VEHICLE IN MOTION"),
        _signature("m2", mode="FAILURE TO START", state="PARKED", indicators=()),
        _signature("m3", mode="CONTACTOR FAILURE", state=None, indicators=()),
    ]
    cluster = ComplaintCluster(
        cluster_id="meta-test",
        label="META — High-voltage power distribution / contactor",
        system="CROSS-COMPONENT",
        failure_mode="HIGH-VOLTAGE POWER DISTRIBUTION / CONTACTOR",
        defect_family="HIGH_VOLTAGE_POWER_DISTRIBUTION",
        failure_mechanism="HIGH_VOLTAGE_POWER_DISTRIBUTION",
        consequence_family="GENERAL_ELECTRICAL_FAILURE",
        member_ids=("m1", "m2", "m3"),
        source_systems=("ELECTRICAL SYSTEM", "POWER TRAIN"),
        first_received_date=date(2022, 4, 1),
        last_received_date=date(2022, 5, 1),
        is_meta=True,
        embedding_method=EmbeddingMethod.NIM,
    )
    recall = Recall(
        campaign_number="22V412000",
        component="ELECTRICAL SYSTEM:PROPULSION SYSTEM",
        summary="High voltage battery main contactors may overheat and open while driving.",
        consequence="An open contactor can result in a loss of motive power.",
    )
    details = RecallMatcher().score_target_details(cluster, signatures, recall, complaints)
    assert details["consequence_family_best"] == 1.0
    assert details["consequence_family_weighted"] > 0.0
    assert details["consequence_family"] > 0.0


@pytest.mark.asyncio
async def test_time_machine_exposes_target_like_candidate_separately_from_qualified_alert(tmp_path) -> None:
    vehicle, complaints, recalls, target, official_date = build_demo_records()
    pipeline = build_pipeline(Settings(data_dir=tmp_path, use_nim=False), use_nim=False)
    result = await RecallTimeMachine(pipeline, target_match_threshold=0.10).run(
        vehicle=vehicle,
        complaints=complaints,
        recalls=recalls,
        target_recall=target,
        official_recall_date=official_date,
        replay_start_date=date(2022, 3, 1),
        alert_threshold=99.9,
        minimum_evidence=4,
        save=False,
    )
    assert result.earliest_target_like_candidate_date is not None
    assert result.earliest_target_like_candidate_score is not None
    assert result.first_qualified_alert_date is None
    assert result.first_matching_alert_date is None
