from datetime import date, timedelta

import pytest

from recallzero.analytics import SeverityEngine, TrendEngine
from recallzero.config import Settings
from recallzero.intelligence.taxonomy import derive_consequence_family, derive_failure_mechanism
from recallzero.models import Complaint, ExtractionMethod, FailureSignature, Vehicle
from recallzero.pipeline import build_pipeline


def _vehicle() -> Vehicle:
    return Vehicle(make="DEMO", model="EV", model_years=(2022,))


def _signature(complaint_id: str, subsystem: str, mode: str, state: str | None = None) -> FailureSignature:
    return FailureSignature(
        complaint_id=complaint_id,
        system="ELECTRICAL SYSTEM",
        subsystem=subsystem,
        failure_mode=mode,
        operating_state=state,
        extraction_method=ExtractionMethod.NIM,
        model_name="test-model",
    )


def test_dual_axis_taxonomy_keeps_mechanism_and_consequence_separate() -> None:
    complaint = Complaint(
        odi_number="1",
        vehicle=_vehicle(),
        received_date=date(2022, 5, 1),
        components=("ELECTRICAL SYSTEM",),
        narrative="High Voltage Battery Junction Box failed after charging. Stop Safely Now; the vehicle would not start or shift into drive.",
    )
    signature = _signature("1", "HVBJB", "FAILURE TO START", "PARKED")
    assert derive_failure_mechanism(complaint, signature) == "HIGH_VOLTAGE_POWER_DISTRIBUTION"
    assert derive_consequence_family(complaint, signature) == "NO_START_OR_NO_DRIVE"


def test_event_scoped_severity_does_not_use_background_driving_reference() -> None:
    complaint = Complaint(
        odi_number="2",
        vehicle=_vehicle(),
        received_date=date(2022, 5, 2),
        components=("ELECTRICAL SYSTEM",),
        narrative=(
            "I drove 400 miles earlier in the day. Later, while parked, the vehicle would not start. "
            "I am fortunate that it did not fail while driving on the highway."
        ),
    )
    signature = _signature("2", "HVBJB", "FAILURE TO START", "PARKED").model_copy(
        update={"severity_indicators": ("vehicle_in_motion",)}
    )
    result = SeverityEngine().calculate([complaint], [signature])
    assert "vehicle_in_motion" not in result.indicator_counts


def test_persistence_retains_recent_activity_when_last_week_is_quiet() -> None:
    cutoff = date(2022, 6, 30)
    vehicle = _vehicle()
    complaints = [
        Complaint(
            odi_number=str(index),
            vehicle=vehicle,
            received_date=cutoff - timedelta(days=days),
            components=("ELECTRICAL SYSTEM",),
            narrative="Vehicle lost motive power while driving.",
        )
        for index, days in enumerate((10, 17, 24), start=1)
    ]
    trend = TrendEngine().calculate(complaints, cutoff)
    assert trend.active_weeks_recent_4 == 3
    assert trend.max_consecutive_weeks_recent_8 == 3
    assert trend.persistence_weeks == 3
    assert trend.persistence_score > 0


@pytest.mark.asyncio
async def test_meta_signal_reconnects_cross_component_hv_presentations(tmp_path) -> None:
    vehicle = _vehicle()
    complaints = [
        Complaint(
            odi_number="e1",
            vehicle=vehicle,
            received_date=date(2022, 4, 3),
            components=("ELECTRICAL SYSTEM",),
            narrative="High Voltage Battery Junction Box failure. Stop Safely Now and vehicle would not start.",
        ),
        Complaint(
            odi_number="p1",
            vehicle=vehicle,
            received_date=date(2022, 4, 18),
            components=("POWER TRAIN",),
            narrative="While driving the HVBJB contactor opened and the vehicle lost motive power.",
        ),
        Complaint(
            odi_number="f1",
            vehicle=vehicle,
            received_date=date(2022, 5, 1),
            components=("FUEL/PROPULSION SYSTEM",),
            narrative="High voltage battery junction box fault left the vehicle unable to shift into drive.",
        ),
        Complaint(
            odi_number="e2",
            vehicle=vehicle,
            received_date=date(2022, 5, 15),
            components=("ELECTRICAL SYSTEM",),
            narrative="HVBJB contactor failure caused Stop Safely Now and a loss of propulsion while driving.",
        ),
    ]
    signatures = [
        _signature("e1", "HVBJB", "FAILURE TO START", "PARKED"),
        _signature("p1", "HVBJB CONTACTOR", "LOSS OF MOTIVE POWER", "VEHICLE IN MOTION"),
        _signature("f1", "HIGH VOLTAGE BATTERY JUNCTION BOX", "NO DRIVE", "PARKED"),
        _signature("e2", "HVBJB", "CONTACTOR FAILURE", "VEHICLE IN MOTION"),
    ]
    pipeline = build_pipeline(Settings(data_dir=tmp_path, use_nim=False), use_nim=False)
    run = await pipeline.analyze_records(
        vehicle=vehicle,
        complaints=complaints,
        recalls=[],
        cutoff_date=date(2022, 5, 31),
        signatures=signatures,
        save=False,
    )
    meta = [
        signal
        for signal in run.signals
        if signal.signal_scope == "meta" and signal.cluster.failure_mechanism == "HIGH_VOLTAGE_POWER_DISTRIBUTION"
    ]
    assert len(meta) == 1
    assert meta[0].cluster.evidence_count == 4
    assert set(meta[0].cluster.source_systems) == {
        "ELECTRICAL SYSTEM",
        "POWER TRAIN",
        "FUEL/PROPULSION SYSTEM",
    }
    assert len(meta[0].cluster.source_cluster_ids) >= 2
    assert run.meta_signal_count >= 1
