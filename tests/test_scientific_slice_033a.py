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


MACH_E_MOVING_REGRESSION_NARRATIVES = {
    "11415152": (
        "AS I WAS DRIVING DOWN THE ROAD (IN MOTION / CITY STREET) AT 30 MILES PER HOUR IN MY BRAND NEW "
        "ALL ELECTRIC FORD MACH E, THE DISPLAY FLASHED MULTIPLE WARNING LIGHTS THAT CAME TOO QUICKLY TO "
        "DECIPHER, AND THEN THE CAR WENT DEAD. I WAS ABLE TO PULL OFF THE ROAD AND COME TO A STOP."
    ),
    "11460408": (
        "On 4/1, while entering the FWY @ about 50 mph under moderate accel, big thump, drivers display "
        "cycled thru multiple errors with Stop Safely Now repeatedly flashing. Throttle went dead, speed "
        "dropped to 35 and steadily decreased. Was able to slowly exit FWY at next exit."
    ),
    "11462003": (
        "While driving around a curve, my car lost all power and went completely dark. I didn't have brakes, "
        "steering, lights or any other functions. My car was completely bricked."
    ),
    "11465461": (
        "Travelling from WI to NC using Electrify America DC fast chargers along the way. While in NC, got "
        "Stop Safely Now alert on screen and had to immediately pull over and stop where there was no shoulder. "
        "Could not move vehicle in any way after this. Could not put it in N, could not put it in Emergency Tow Mode."
    ),
    "11465548": (
        "On May 20, 2022, for the second time in a month my 2021 California 1 Mach e has had a catastrophic "
        "failure. First time I pulled out onto road after fast charging. Tonight, I started driving out of "
        "parking space after shopping. Emergency bells go off, all kinds of alerts appear, and the car dies."
    ),
    "11466150": (
        "Driving our BEV Mach E at a normal speed, we got an error code, Pull to the side of the road safely / "
        "stop safely, on our instrument display. The car turned itself off, and we had to slowly coast to the "
        "side of the road."
    ),
}

# Production-shaped parked regressions from the Mach-E replay. 11459506 deliberately
# keeps the later/background 'while driving' phrase that triggered the 0.3.3a2 bug.
MACH_E_PARKED_REGRESSION_NARRATIVES = {
    "11459506": (
        'Car was parked in my home garage and would not start, displaying "Stop safely now." Vehicle appears '
        "to have code P0AA1 Hybrid battery positive contactor - circuit stuck closed. Many other Mach-E vehicles "
        "are exhibiting this defective part in the high voltage battery system. Owners have also reported this "
        "part failing while driving."
    ),
    "11463755": (
        "I was stranded in a parking lot in the evening when my car wouldn't start. The vehicle displayed a "
        '"STOP SAFELY NOW" message and I could not get the car to move on its own. I had to call and wait for a tow truck.'
    ),
    "11464507": (
        'Vehicle would not start after a brief stop, with a "Stop Safely Now" error. Vehicle could not be shifted '
        "out of Park, and could only be shifted into Neutral using Emergency Tow Mode. The High-Voltage Battery "
        "Junction Box failed and required replacement."
    ),
    "11464559": (
        "I received a Stop Safely Now message upon returning to my vehicle at the end of a work day. The vehicle "
        "would not shift into drive, but accessory power did work. I was forced to leave the vehicle in the "
        "parking structure overnight and it later required a tow."
    ),
}


@pytest.mark.parametrize("odi", sorted(MACH_E_MOVING_REGRESSION_NARRATIVES))
def test_named_mach_e_moving_failures_validate_loss_of_motive_power(odi: str) -> None:
    complaint = _complaint(odi, MACH_E_MOVING_REGRESSION_NARRATIVES[odi])
    evidence = SeverityEngine().validate_indicators(complaint, _signature(odi))
    assert "loss_of_motive_power" in evidence
    assert "vehicle_in_motion" in evidence


@pytest.mark.parametrize("odi", sorted(MACH_E_PARKED_REGRESSION_NARRATIVES))
def test_named_mach_e_parked_failures_do_not_become_moving_power_loss(odi: str) -> None:
    complaint = _complaint(odi, MACH_E_PARKED_REGRESSION_NARRATIVES[odi])
    signature = _signature(odi, state="PARKED")
    engine = SeverityEngine()
    evidence = engine.validate_indicators(complaint, signature)
    context = engine.validate_context(complaint, signature)
    assert "loss_of_motive_power" not in evidence
    assert "vehicle_in_motion" not in evidence
    assert context["event_state_source"] == "suppressed_by_parked_event"


def test_11459506_bug_shape_rejects_background_while_driving_sentence() -> None:
    complaint = _complaint("11459506", MACH_E_PARKED_REGRESSION_NARRATIVES["11459506"])
    signature = _signature("11459506", state="PARKED", indicators=("vehicle_in_motion",))
    engine = SeverityEngine()
    assert engine._parked_event(complaint, signature) is True
    assert engine._motion_evidence(complaint, signature) is None
    assert engine.validate_indicators(complaint, signature) == {}


def test_split_sentence_moving_failure_survives_later_parked_context() -> None:
    narrative = (
        "I was driving on the highway at 60 mph. The car suddenly lost all power. "
        "I coasted to the shoulder and parked the vehicle. It would not restart after I parked."
    )
    complaint = _complaint("split-moving", narrative)
    signature = _signature("split-moving", state="VEHICLE IN MOTION")
    engine = SeverityEngine()
    evidence = engine.validate_indicators(complaint, signature)
    assert "vehicle_in_motion" in evidence
    assert "loss_of_motive_power" in evidence
    assert engine.validate_context(complaint, signature)["event_state_source"] == "incident_motion_context"


def test_severity_83_5_is_unchanged_after_parked_motion_false_positive_is_removed() -> None:
    moving_a = _complaint("moving-a", "While driving, the car lost all power and coasted to a stop.")
    moving_b = _complaint("moving-b", "I was driving on the road. The vehicle shut down and lost all power.")
    parked = _complaint(
        "parked-fp",
        "Car was parked in my garage and would not start. Other owners report failures while driving.",
    )
    signatures = [
        _signature("moving-a"),
        _signature("moving-b"),
        _signature("parked-fp", state="PARKED", indicators=("vehicle_in_motion",)),
    ]
    result = SeverityEngine().calculate([moving_a, moving_b, parked], signatures)
    assert result.indicator_counts["loss_of_motive_power"] == 2
    assert result.indicator_counts["vehicle_in_motion"] == 2
    assert result.score == 83.5
    assert result.context_by_complaint["parked-fp"]["event_state_source"] == "suppressed_by_parked_event"


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
