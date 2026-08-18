from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

import recallzero.benchmark as benchmark_mod
import recallzero.benchmark_adjudication as adjudication_mod
from recallzero.benchmark import (
    BenchmarkAcceptanceCriteria,
    BenchmarkCandidateConfig,
    BenchmarkCandidateRecord,
    BenchmarkCaseResult,
    BenchmarkManifestLock,
    BenchmarkRunResult,
    BenchmarkSnapshotRecord,
    _case_alert_metrics,
    _input_provenance,
    aggregate_benchmark,
    create_benchmark_lock,
    load_candidates,
    verify_benchmark_lock,
    wilson_interval,
)
from recallzero.benchmark_adjudication import (
    AdjudicatedBenchmarkCase,
    AlertAdjudication,
    aggregate_adjudicated,
    adjudicate_benchmark,
)
from recallzero.config import Settings
from recallzero.freeze import FreezeVerification, sha256_file
from recallzero.models import Complaint, FailureSignature, Recall, Vehicle


def _write_manifest(path: Path, *, negative_name: str = "Control") -> None:
    path.write_text(
        f"""
benchmark:
  id: detector-v1-test
  preregistered: true
candidates:
  - name: Positive
    make: DEMO
    model: CAR
    model_years: [2022]
    campaign_number: 22V999000
    official_recall_date: 2022-06-10
    expected_role: positive
    benchmark_split: validation
  - name: {negative_name}
    make: DEMO
    model: CONTROL
    model_years: [2022]
    evaluation_end_date: 2023-01-01
    adjudication_end_date: 2024-01-01
    expected_role: negative
    benchmark_split: validation
  - name: Holdout
    make: DEMO
    model: HOLDOUT
    model_years: [2022]
    evaluation_end_date: 2023-01-01
    adjudication_end_date: 2024-01-01
    expected_role: negative
    benchmark_split: holdout
""",
        encoding="utf-8",
    )


def _write_freeze(path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "freeze_id": "recallzero-detector-v1",
                "evaluation": {"target_match_threshold": 0.45, "top_candidate_count": 5},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _vehicle(model: str = "CONTROL") -> Vehicle:
    return Vehicle(make="DEMO", model=model, model_years=(2022,))


def _candidate(*, risk: float = 80.23, lineage: str = "lin-1", alert: bool = True) -> BenchmarkCandidateRecord:
    return BenchmarkCandidateRecord(
        signal_id=f"sig-{lineage}",
        lineage_id=lineage,
        signal_scope="meta",
        issue="Synthetic alert",
        failure_mechanism="PROPULSION_CONTROL",
        consequence_family="LOSS_OF_MOTIVE_POWER",
        evidence_count=4,
        risk_score=risk,
        risk_factors={
            "severity": {"score": 80.0, "weight": 0.30, "contribution": 24.0},
            "trend": {"score": 70.0, "weight": 0.25, "contribution": 17.5},
        },
        alert=alert,
        distance_to_alert_threshold=max(0.0, 75.0 - risk),
        threshold_margin=round(risk - 75.0, 2),
    )


def test_validation_split_parses_and_filters() -> None:
    candidate = BenchmarkCandidateConfig(
        name="Validation",
        make="DEMO",
        model="CAR",
        model_years=(2022,),
        campaign_number="22V999000",
        official_recall_date=date(2022, 6, 10),
        expected_role="positive",
        benchmark_split="validation",
    )
    assert candidate.benchmark_split == "validation"


def test_negative_boundaries_are_required_and_ordered() -> None:
    base = dict(
        name="Control",
        make="DEMO",
        model="CAR",
        model_years=(2022,),
        expected_role="negative",
        benchmark_split="validation",
    )
    with pytest.raises(ValidationError, match="evaluation_end_date"):
        BenchmarkCandidateConfig(**base)
    with pytest.raises(ValidationError, match="adjudication_end_date"):
        BenchmarkCandidateConfig(**base, evaluation_end_date=date(2023, 1, 1))
    with pytest.raises(ValidationError, match="must be after"):
        BenchmarkCandidateConfig(
            **base,
            evaluation_end_date=date(2023, 1, 1),
            adjudication_end_date=date(2023, 1, 1),
        )


def test_load_candidates_filters_split(tmp_path: Path) -> None:
    manifest = tmp_path / "candidates.yml"
    _write_manifest(manifest)
    validation = load_candidates(manifest, split="validation")
    holdout = load_candidates(manifest, split="holdout")
    assert len(validation) == 2
    assert all(item.benchmark_split == "validation" for item in validation)
    assert len(holdout) == 1


@pytest.mark.asyncio
async def test_holdout_requires_explicit_confirmation(tmp_path: Path) -> None:
    manifest = tmp_path / "candidates.yml"
    freeze = tmp_path / "freeze.yml"
    _write_manifest(manifest)
    _write_freeze(freeze)
    with pytest.raises(RuntimeError, match="explicit confirmation"):
        await benchmark_mod.run_benchmark(
            settings=Settings(data_dir=tmp_path / "data", use_nim=False),
            candidates_path=manifest,
            freeze_manifest_path=freeze,
            split="holdout",
        )


def test_manifest_lock_detects_candidate_change(tmp_path: Path) -> None:
    manifest = tmp_path / "candidates.yml"
    freeze = tmp_path / "freeze.yml"
    lock_path = tmp_path / "lock.json"
    _write_manifest(manifest)
    _write_freeze(freeze)
    lock = create_benchmark_lock(
        candidates_path=manifest,
        freeze_manifest_path=freeze,
        split="validation",
    )
    lock_path.write_text(lock.model_dump_json(indent=2), encoding="utf-8")
    assert verify_benchmark_lock(
        lock_path=lock_path,
        candidates_path=manifest,
        freeze_manifest_path=freeze,
        split="validation",
    ).ok

    original_hash = lock.candidate_manifest_sha256
    _write_manifest(manifest, negative_name="Changed Control")
    assert sha256_file(manifest) != original_hash
    result = verify_benchmark_lock(
        lock_path=lock_path,
        candidates_path=manifest,
        freeze_manifest_path=freeze,
        split="validation",
    )
    assert not result.ok
    assert any("candidate manifest hash" in item for item in result.errors)


def test_signed_threshold_margin_and_alert_persistence() -> None:
    snapshots = (
        BenchmarkSnapshotRecord(
            cutoff_date=date(2022, 5, 19),
            complaint_count_visible=4,
            signal_count=1,
            max_risk_score=74.8,
            threshold_margin=-0.2,
            top_candidates=(_candidate(risk=74.8, alert=False),),
        ),
        BenchmarkSnapshotRecord(
            cutoff_date=date(2022, 5, 26),
            complaint_count_visible=5,
            signal_count=1,
            max_risk_score=80.23,
            threshold_margin=5.23,
            alert_signal_ids=("sig-lin-1",),
            alert_lineage_ids=("lin-1",),
            top_candidates=(_candidate(risk=80.23),),
        ),
        BenchmarkSnapshotRecord(
            cutoff_date=date(2022, 6, 2),
            complaint_count_visible=6,
            signal_count=1,
            max_risk_score=79.0,
            threshold_margin=4.0,
            alert_signal_ids=("sig-lin-1",),
            alert_lineage_ids=("lin-1",),
            top_candidates=(_candidate(risk=79.0),),
        ),
    )
    metrics = _case_alert_metrics(snapshots)
    assert snapshots[0].threshold_margin == -0.2
    assert snapshots[1].threshold_margin == 5.23
    assert metrics["first_alert_date"] == date(2022, 5, 26)
    assert metrics["last_alert_date"] == date(2022, 6, 2)
    assert metrics["max_consecutive_alert_snapshots"] == 2
    assert metrics["max_alert_duration_days"] == 7
    assert metrics["first_alert_threshold_margin"] == 5.23


def test_wilson_intervals_cover_edge_and_partial_cases() -> None:
    zero = wilson_interval(0, 10)
    full = wilson_interval(10, 10)
    partial = wilson_interval(7, 10)
    assert zero is not None and zero["low"] == 0.0 and zero["high"] > 0
    assert full is not None and full["high"] == 1.0 and full["low"] < 1
    assert partial is not None and partial["low"] < 0.7 < partial["high"]
    assert wilson_interval(0, 0) is None


def test_input_fingerprint_changes_when_evidence_changes() -> None:
    vehicle = _vehicle("CAR")
    complaint = Complaint(
        odi_number="ODI1",
        vehicle=vehicle,
        received_date=date(2022, 1, 1),
        components=("POWER TRAIN",),
        narrative="Vehicle stalled while driving.",
    )
    signature = FailureSignature(
        complaint_id="ODI1",
        system="POWER TRAIN",
        failure_mode="STALL",
        confidence=0.9,
    )
    first = _input_provenance(
        complaints=[complaint], recalls=[], signatures=[signature], boundary=date(2023, 1, 1)
    )
    changed = complaint.model_copy(update={"narrative": "Vehicle stalled and would not restart."})
    second = _input_provenance(
        complaints=[changed], recalls=[], signatures=[signature], boundary=date(2023, 1, 1)
    )
    assert first.complaint_dataset_sha256 != second.complaint_dataset_sha256
    assert first.eligible_complaint_dataset_sha256 != second.eligible_complaint_dataset_sha256


def test_invalid_infrastructure_cases_are_excluded_but_detector_miss_is_valid() -> None:
    miss = BenchmarkCaseResult(
        case_id="miss",
        name="genuine miss",
        expected_role="positive",
        benchmark_split="validation",
        vehicle=_vehicle("MISS"),
        campaign_number="22V999000",
        boundary_date=date(2022, 6, 10),
        status="NO_EARLY_SIGNAL",
        benchmark_valid=True,
    )
    invalid = BenchmarkCaseResult(
        case_id="invalid",
        name="infra",
        expected_role="positive",
        benchmark_split="validation",
        vehicle=_vehicle("INVALID"),
        campaign_number="22V998000",
        boundary_date=date(2022, 6, 10),
        status="INVALID_CASE",
        benchmark_valid=False,
        invalid_reason="NIM_EXTRACTION_FAILURE",
    )
    aggregate = aggregate_benchmark([miss, invalid])
    assert aggregate["valid_positive_count"] == 1
    assert aggregate["invalid_case_count"] == 1
    assert aggregate["positive_sensitivity"] == 0.0


@pytest.mark.asyncio
async def test_preflight_detects_campaign_date_mismatch_and_duplicate_case_id(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "candidates.yml"
    freeze = tmp_path / "freeze.yml"
    manifest.write_text(
        """
benchmark: {id: preflight-test}
candidates:
  - &case
    name: A
    make: DEMO
    model: CAR
    model_years: [2022]
    campaign_number: 22V999000
    official_recall_date: 2022-06-10
    expected_role: positive
    benchmark_split: validation
  - <<: *case
    name: B
""",
        encoding="utf-8",
    )
    _write_freeze(freeze)
    vehicle = _vehicle("CAR")
    complaint = Complaint(
        odi_number="ODI1",
        vehicle=vehicle,
        received_date=date(2022, 1, 1),
        components=("POWER TRAIN",),
        narrative="Vehicle stalled while driving.",
    )
    recall = Recall(
        campaign_number="22V999000",
        vehicle=vehicle,
        report_received_date=date(2022, 6, 9),
        component="POWER TRAIN",
        summary="Engine may stall.",
    )

    class FakePipeline:
        nhtsa = SimpleNamespace(fetch_campaign=lambda *args, **kwargs: None)

        async def ingest(self, _vehicle, refresh=False):
            return [complaint], [recall]

    monkeypatch.setattr(
        benchmark_mod,
        "verify_freeze",
        lambda *args, **kwargs: FreezeVerification(freeze_id="recallzero-detector-v1", ok=True),
    )
    result = await benchmark_mod.preflight_benchmark(
        settings=Settings(data_dir=tmp_path / "data", use_nim=False),
        candidates_path=manifest,
        freeze_manifest_path=freeze,
        split="validation",
        pipeline_factory=lambda settings, use_nim=False: FakePipeline(),
    )
    assert not result.ready
    assert result.duplicate_case_ids
    assert all(any("campaign date mismatch" in err for err in row.errors) for row in result.cases)
    assert all("duplicate case_id" in row.errors for row in result.cases)


def _raw_control_run(tmp_path: Path, manifest: Path, freeze: Path) -> tuple[Path, BenchmarkRunResult]:
    case_cfg = load_candidates(manifest, split="validation")[1]
    snapshot = BenchmarkSnapshotRecord(
        cutoff_date=date(2022, 12, 1),
        complaint_count_visible=4,
        signal_count=1,
        max_risk_score=80.0,
        threshold_margin=5.0,
        alert_signal_ids=("sig-lin-control",),
        alert_lineage_ids=("lin-control",),
        top_candidates=(_candidate(risk=80.0, lineage="lin-control"),),
    )
    metrics = _case_alert_metrics((snapshot,))
    case = BenchmarkCaseResult(
        case_id=case_cfg.case_id,
        name=case_cfg.name,
        expected_role="negative",
        benchmark_split="validation",
        vehicle=case_cfg.vehicle,
        boundary_date=case_cfg.boundary_date,
        adjudication_end_date=case_cfg.adjudication_end_date,
        status="CONTROL_ALERT_REQUIRES_ADJUDICATION",
        first_any_alert_date=date(2022, 12, 1),
        alert_snapshot_count=1,
        alert_signal_occurrences=1,
        replay_days=365,
        replay_years=1.0,
        anti_leakage_checks={
            "complaints_strictly_before_boundary": True,
            "snapshots_strictly_before_boundary": True,
            "no_target_recall_used": True,
        },
        snapshots=(snapshot,),
        **metrics,
    )
    raw = BenchmarkRunResult(
        benchmark_run_id="run-1",
        benchmark_id="detector-v1-test",
        benchmark_split="validation",
        freeze_id="recallzero-detector-v1",
        freeze_verified=True,
        lock_verified=True,
        manifest_path=str(manifest),
        manifest_sha256=sha256_file(manifest),
        freeze_manifest_path=str(freeze),
        case_count=1,
        cases=(case,),
        aggregate=aggregate_benchmark([case]),
        acceptance_criteria=BenchmarkAcceptanceCriteria(),
    )
    raw_path = tmp_path / "raw.json"
    raw_path.write_text(raw.model_dump_json(indent=2), encoding="utf-8")
    return raw_path, raw


@pytest.mark.asyncio
async def test_future_recall_adjudication_does_not_change_frozen_risk(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "candidates.yml"
    freeze = tmp_path / "freeze.yml"
    _write_manifest(manifest)
    _write_freeze(freeze)
    raw_path, raw = _raw_control_run(tmp_path, manifest, freeze)
    future_recall = Recall(
        campaign_number="23V123000",
        report_received_date=date(2023, 6, 1),
        component="POWER TRAIN",
        summary="Vehicle may lose motive power.",
        consequence="Loss of motive power can increase crash risk.",
    )
    monkeypatch.setattr(
        adjudication_mod,
        "verify_freeze",
        lambda *args, **kwargs: FreezeVerification(freeze_id="recallzero-detector-v1", ok=True),
    )

    async def fake_recalls(*args, **kwargs):
        return [future_recall]

    monkeypatch.setattr(adjudication_mod, "_recalls_for_case", fake_recalls)
    monkeypatch.setattr(
        adjudication_mod.RecallMatcher,
        "score_target_details",
        lambda self, *args, **kwargs: {"final": 0.80, "component": 0.9},
    )
    adjudicated = await adjudicate_benchmark(
        settings=Settings(data_dir=tmp_path / "data", use_nim=False),
        raw_result_path=raw_path,
        candidates_path=manifest,
    )
    rows = adjudicated.cases[0].alert_adjudications
    assert len(rows) == 1
    assert rows[0].classification == "FUTURE_RECALL_ASSOCIATED"
    assert adjudicated.aggregate["unconfirmed_control_alerts"] == 0
    assert adjudicated.aggregate["future_recall_associated_control_alerts"] == 1
    assert adjudicated.cases[0].case.max_risk_score == raw.cases[0].max_risk_score
    assert adjudicated.cases[0].case.snapshots[0].max_risk_score == 80.0


@pytest.mark.asyncio
async def test_unmatched_control_alert_is_unconfirmed(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "candidates.yml"
    freeze = tmp_path / "freeze.yml"
    _write_manifest(manifest)
    _write_freeze(freeze)
    raw_path, _ = _raw_control_run(tmp_path, manifest, freeze)
    monkeypatch.setattr(
        adjudication_mod,
        "verify_freeze",
        lambda *args, **kwargs: FreezeVerification(freeze_id="recallzero-detector-v1", ok=True),
    )

    async def no_recalls(*args, **kwargs):
        return []

    monkeypatch.setattr(adjudication_mod, "_recalls_for_case", no_recalls)
    adjudicated = await adjudicate_benchmark(
        settings=Settings(data_dir=tmp_path / "data", use_nim=False),
        raw_result_path=raw_path,
        candidates_path=manifest,
    )
    assert adjudicated.cases[0].alert_adjudications[0].classification == "UNCONFIRMED_ALERT"
    assert adjudicated.aggregate["unconfirmed_control_alerts"] == 1
    assert adjudicated.aggregate["control_cases_with_unconfirmed_alerts"] == 1


def test_adjudicated_aggregate_does_not_count_future_recall_as_unconfirmed() -> None:
    case = BenchmarkCaseResult(
        case_id="control",
        name="control",
        expected_role="negative",
        benchmark_split="validation",
        vehicle=_vehicle(),
        boundary_date=date(2023, 1, 1),
        adjudication_end_date=date(2024, 1, 1),
        status="CONTROL_ALERT_REQUIRES_ADJUDICATION",
        benchmark_valid=True,
        first_alert_date=date(2022, 12, 1),
        replay_years=1.0,
        anti_leakage_checks={"no_target_recall_used": True},
    )
    future = AlertAdjudication(
        lineage_id="lin-1",
        first_alert_date=date(2022, 12, 1),
        last_alert_date=date(2022, 12, 1),
        snapshot_count=1,
        classification="FUTURE_RECALL_ASSOCIATED",
        campaign_number="23V123000",
        recall_date=date(2023, 6, 1),
    )
    result = aggregate_adjudicated(
        [AdjudicatedBenchmarkCase(case=case, alert_adjudications=(future,))],
        acceptance=BenchmarkAcceptanceCriteria(),
        freeze_verified=True,
        lock_verified=True,
    )
    assert result["future_recall_associated_control_alerts"] == 1
    assert result["unconfirmed_control_alerts"] == 0
    assert result["unconfirmed_alert_lineages_per_vehicle_replay_year"] == 0.0

@pytest.mark.asyncio
async def test_synthetic_benchmark_positive_and_quiet_control_flow(tmp_path: Path, monkeypatch) -> None:
    """Offline E2E smoke: frozen evaluation wrapper around the real detector pipeline."""
    from recallzero.demo import build_demo_records
    from recallzero.pipeline import build_pipeline

    positive_vehicle, positive_complaints, positive_recalls, _target, official_date = build_demo_records()
    quiet_vehicle = Vehicle(make="DEMO MOTORS", model="QUIET", model_years=(2022,))
    quiet_complaints = [
        Complaint(
            odi_number=f"QUIET{i}",
            vehicle=quiet_vehicle,
            received_date=date(2022, month, 1),
            components=("EQUIPMENT",),
            narrative="Infotainment audio briefly rebooted and returned to normal.",
            raw_payload={"synthetic": True},
        )
        for i, month in enumerate((1, 3, 5), start=1)
    ]

    manifest = tmp_path / "synthetic.yml"
    manifest.write_text(
        f"""
benchmark:
  id: synthetic-validation
  preregistered: true
  acceptance_criteria:
    minimum_nim_fraction: 0.0
candidates:
  - name: Synthetic positive
    make: DEMO MOTORS
    model: VECTOR EV
    model_years: [2021, 2022]
    campaign_number: 22V999000
    official_recall_date: {official_date.isoformat()}
    expected_role: positive
    benchmark_split: validation
  - name: Synthetic quiet control
    make: DEMO MOTORS
    model: QUIET
    model_years: [2022]
    evaluation_end_date: 2022-06-10
    adjudication_end_date: 2023-06-10
    expected_role: negative
    benchmark_split: validation
""",
        encoding="utf-8",
    )
    freeze = tmp_path / "freeze.yml"
    freeze.write_text(
        yaml.safe_dump(
            {
                "freeze_id": "recallzero-detector-v1",
                "evaluation": {"target_match_threshold": 0.10, "top_candidate_count": 5},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    lock_path = tmp_path / "lock.json"
    lock = create_benchmark_lock(
        candidates_path=manifest,
        freeze_manifest_path=freeze,
        split="validation",
    )
    lock_path.write_text(lock.model_dump_json(indent=2), encoding="utf-8")
    monkeypatch.setattr(
        benchmark_mod,
        "verify_freeze",
        lambda *args, **kwargs: FreezeVerification(freeze_id="recallzero-detector-v1", ok=True),
    )

    def factory(settings, use_nim=False):
        pipeline = build_pipeline(settings, use_nim=False)

        async def ingest(vehicle, refresh=False):
            if vehicle.model == positive_vehicle.model:
                return list(positive_complaints), list(positive_recalls)
            return list(quiet_complaints), []

        pipeline.ingest = ingest  # type: ignore[method-assign]
        return pipeline

    risk_file = tmp_path / "risk.yml"
    risk_file.write_text(
        """
risk:
  weights:
    severity: 0.30
    trend: 0.25
    persistence: 0.15
    evidence: 0.20
    recall_gap: 0.10
  alert_threshold: 50.0
  minimum_evidence: 4
  medium_threshold: 45.0
  high_threshold: 70.0
  critical_threshold: 88.0
""",
        encoding="utf-8",
    )
    result, verification = await benchmark_mod.run_benchmark(
        settings=Settings(data_dir=tmp_path / "data", risk_file=risk_file, use_nim=False),
        candidates_path=manifest,
        freeze_manifest_path=freeze,
        split="validation",
        lock_path=lock_path,
        pipeline_factory=factory,
    )
    assert verification.ok
    assert result.lock_verified
    assert len(result.cases) == 2
    positive = next(item for item in result.cases if item.expected_role == "positive")
    quiet = next(item for item in result.cases if item.expected_role == "negative")
    assert positive.benchmark_valid
    assert positive.first_qualified_alert_date is not None
    assert positive.lead_time_days is not None and positive.lead_time_days > 0
    assert all(positive.anti_leakage_checks.values())
    assert quiet.benchmark_valid
    assert quiet.status == "CONTROL_QUIET"
    assert quiet.first_alert_date is None


@pytest.mark.asyncio
async def test_preflight_surfaces_complaint_catalog_provenance(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "candidates.yml"
    freeze = tmp_path / "freeze.yml"
    manifest.write_text(
        """
benchmark: {id: preflight-provenance}
candidates:
  - name: Variant vehicle
    make: FORD
    model: F-150
    model_years: [2016]
    campaign_number: 20V332000
    official_recall_date: 2020-06-08
    expected_role: positive
    benchmark_split: validation
""",
        encoding="utf-8",
    )
    _write_freeze(freeze)
    vehicle = Vehicle(make="FORD", model="F-150", model_years=(2016,))
    complaint = Complaint(
        odi_number="ODI-F150",
        vehicle=vehicle,
        received_date=date(2016, 1, 1),
        components=("SERVICE BRAKES",),
        narrative="Brake pedal became soft while driving.",
    )
    recall = Recall(
        campaign_number="20V332000",
        vehicle=vehicle,
        report_received_date=date(2020, 6, 8),
        component="SERVICE BRAKES, HYDRAULIC",
        summary="Brake master cylinder may leak.",
    )
    raw = {
        "2016": {
            "count": 1,
            "results": [],
            "recallzeroQuery": {
                "adapterRevision": "nhtsa-complaint-catalog-v2",
                "requestedModel": "F-150",
                "catalogModelsResolved": ["F-150", "F-150 SUPERCAB", "F-150 SUPERCREW"],
                "queriedModels": ["F-150", "F-150 SUPERCAB", "F-150 SUPERCREW"],
                "countByModelVariant": {"F-150": 0, "F-150 SUPERCAB": 1, "F-150 SUPERCREW": 0},
            },
        }
    }

    class FakeRepository:
        def load_raw(self, _vehicle, kind):
            assert kind == "complaints"
            return raw

    class FakePipeline:
        repository = FakeRepository()
        nhtsa = SimpleNamespace(fetch_campaign=lambda *args, **kwargs: None)

        async def ingest(self, _vehicle, refresh=False):
            return [complaint], [recall]

    monkeypatch.setattr(
        benchmark_mod,
        "verify_freeze",
        lambda *args, **kwargs: FreezeVerification(freeze_id="recallzero-detector-v1", ok=True),
    )
    result = await benchmark_mod.preflight_benchmark(
        settings=Settings(data_dir=tmp_path / "data", use_nim=False),
        candidates_path=manifest,
        freeze_manifest_path=freeze,
        split="validation",
        pipeline_factory=lambda settings, use_nim=False: FakePipeline(),
    )

    assert result.ready
    row = result.cases[0]
    assert row.complaint_adapter_revision == "nhtsa-complaint-catalog-v2"
    assert row.complaint_models_requested == ("F-150",)
    assert row.complaint_models_resolved == ("F-150", "F-150 SUPERCAB", "F-150 SUPERCREW")
    assert row.complaint_count_by_model_variant["2016:F-150 SUPERCAB"] == 1


@pytest.mark.asyncio
async def test_preflight_rejects_stale_complaint_cache_without_adapter_provenance(
    tmp_path: Path, monkeypatch
) -> None:
    manifest = tmp_path / "candidates.yml"
    freeze = tmp_path / "freeze.yml"
    manifest.write_text(
        """
benchmark: {id: stale-input}
candidates:
  - name: Stale case
    make: DEMO
    model: CAR
    model_years: [2022]
    campaign_number: 22V999000
    official_recall_date: 2022-06-10
    expected_role: positive
    benchmark_split: validation
""",
        encoding="utf-8",
    )
    _write_freeze(freeze)
    vehicle = _vehicle("CAR")
    complaint = Complaint(
        odi_number="ODI1",
        vehicle=vehicle,
        received_date=date(2022, 1, 1),
        components=("POWER TRAIN",),
        narrative="Vehicle stalled while driving.",
    )
    recall = Recall(
        campaign_number="22V999000",
        vehicle=vehicle,
        report_received_date=date(2022, 6, 10),
        component="POWER TRAIN",
        summary="Engine may stall.",
    )

    class FakeRepository:
        def load_raw(self, _vehicle, kind):
            assert kind == "complaints"
            return {"2022": {"count": 1, "results": []}}

    class FakePipeline:
        repository = FakeRepository()
        nhtsa = SimpleNamespace(fetch_campaign=lambda *args, **kwargs: None)

        async def ingest(self, _vehicle, refresh=False):
            return [complaint], [recall]

    monkeypatch.setattr(
        benchmark_mod,
        "verify_freeze",
        lambda *args, **kwargs: FreezeVerification(freeze_id="recallzero-detector-v1", ok=True),
    )
    result = await benchmark_mod.preflight_benchmark(
        settings=Settings(data_dir=tmp_path / "data", use_nim=False),
        candidates_path=manifest,
        freeze_manifest_path=freeze,
        split="validation",
        pipeline_factory=lambda settings, use_nim=False: FakePipeline(),
    )

    assert not result.ready
    assert any("rerun preflight with --refresh" in error for error in result.cases[0].errors)
