from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from recallzero.backtest import RecallTimeMachine
from recallzero.config import Settings
from recallzero.freeze import FreezeVerification, load_freeze_manifest, sha256_file, verify_freeze
from recallzero.models import Complaint, FailureSignature, Recall, Vehicle
from recallzero.pipeline import RecallZeroPipeline, build_pipeline
from recallzero.utils import stable_id

BenchmarkSplit = Literal["development", "validation", "holdout"]
ExpectedRole = Literal["positive", "negative"]


class BenchmarkAcceptanceCriteria(BaseModel):
    """Pre-registered prototype criteria for the validation cohort.

    These are evaluation thresholds, not detector thresholds. They are intentionally
    stored with the benchmark manifest so they cannot be invented after seeing results.
    """

    positive_sensitivity_min: float = Field(default=0.60, ge=0.0, le=1.0)
    median_qualified_lead_time_days_min: float = Field(default=1.0, ge=0.0)
    unconfirmed_alert_lineages_per_vehicle_replay_year_max: float = Field(default=1.0, ge=0.0)
    control_cases_with_unconfirmed_alerts_rate_max: float = Field(default=0.30, ge=0.0, le=1.0)
    anti_leakage_required: bool = True
    minimum_nim_fraction: float = Field(default=1.0, ge=0.0, le=1.0)


class BenchmarkMetadata(BaseModel):
    id: str = "detector-v1-benchmark"
    detector_freeze: str = "recallzero-detector-v1"
    selection_policy: str = "complaint_addressable_safety_recalls_v1"
    preregistered: bool = False
    acceptance_criteria: BenchmarkAcceptanceCriteria = Field(default_factory=BenchmarkAcceptanceCriteria)


class BenchmarkCandidateConfig(BaseModel):
    name: str
    make: str
    model: str
    model_years: tuple[int, ...]
    campaign_number: str | None = None
    official_recall_date: date | None = None
    evaluation_end_date: date | None = None
    adjudication_end_date: date | None = None
    status: str = "candidate"
    note: str | None = None
    expected_role: ExpectedRole = "positive"
    benchmark_split: BenchmarkSplit = "development"
    defect_family: str | None = None
    selection_stratum: str | None = None
    minimum_eligible_complaints: int | None = Field(default=None, ge=0)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_boundary(self) -> "BenchmarkCandidateConfig":
        if self.expected_role == "positive":
            if not self.campaign_number or not self.official_recall_date:
                raise ValueError("Positive benchmark cases require campaign_number and official_recall_date")
        else:
            if self.evaluation_end_date is None:
                raise ValueError("Negative benchmark cases require evaluation_end_date")
            if self.adjudication_end_date is None:
                raise ValueError("Negative benchmark cases require adjudication_end_date")
            if self.adjudication_end_date <= self.evaluation_end_date:
                raise ValueError("Negative adjudication_end_date must be after evaluation_end_date")
        return self

    @property
    def vehicle(self) -> Vehicle:
        return Vehicle(make=self.make, model=self.model, model_years=self.model_years)

    @property
    def boundary_date(self) -> date:
        if self.expected_role == "positive":
            assert self.official_recall_date is not None
            return self.official_recall_date
        assert self.evaluation_end_date is not None
        return self.evaluation_end_date

    @property
    def case_id(self) -> str:
        return stable_id(
            "case",
            self.expected_role,
            self.vehicle.slug,
            self.campaign_number or "control",
            self.boundary_date,
        )


class BenchmarkManifest(BaseModel):
    benchmark: BenchmarkMetadata = Field(default_factory=BenchmarkMetadata)
    candidates: tuple[BenchmarkCandidateConfig, ...]


class BenchmarkManifestLock(BaseModel):
    benchmark_id: str
    freeze_id: str
    split: BenchmarkSplit
    candidate_manifest_sha256: str
    freeze_manifest_sha256: str
    case_ids: tuple[str, ...]
    case_count: int
    positive_count: int
    control_count: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BenchmarkLockVerification(BaseModel):
    ok: bool
    errors: tuple[str, ...] = Field(default_factory=tuple)


class BenchmarkInputProvenance(BaseModel):
    complaint_count_raw: int = 0
    complaint_count_eligible: int = 0
    recall_count_raw: int = 0
    complaint_dataset_sha256: str | None = None
    eligible_complaint_dataset_sha256: str | None = None
    recall_dataset_sha256: str | None = None
    signature_cache_sha256: str | None = None


class BenchmarkSemanticProvenance(BaseModel):
    signature_count: int = 0
    nim_count: int = 0
    heuristic_count: int = 0
    cached_count: int = 0
    other_count: int = 0
    nim_fraction: float | None = None
    degraded_snapshot_count: int = 0


class BenchmarkAlertEvidence(BaseModel):
    complaint: Complaint
    signature: FailureSignature


class BenchmarkAlertLineageSummary(BaseModel):
    lineage_id: str
    first_date: date
    last_date: date
    snapshot_count: int
    max_consecutive_snapshots: int
    max_consecutive_duration_days: int


class BenchmarkCandidateRecord(BaseModel):
    signal_id: str
    lineage_id: str
    signal_scope: str
    issue: str
    cluster_id: str = ""
    system: str = "UNKNOWN"
    failure_mode: str = "UNSPECIFIED FAILURE"
    defect_family: str = "OTHER"
    failure_mechanism: str
    consequence_family: str
    source_systems: tuple[str, ...] = Field(default_factory=tuple)
    member_ids: tuple[str, ...] = Field(default_factory=tuple)
    embedding_method: str = "tfidf"
    evidence_count: int
    risk_score: float
    risk_factors: dict[str, dict[str, Any]] = Field(default_factory=dict)
    alert: bool
    distance_to_alert_threshold: float
    threshold_margin: float
    visible_recall_matched: bool = False
    visible_recall_campaign_number: str | None = None
    visible_recall_score: float | None = None
    visible_recall_breakdown: dict[str, float] = Field(default_factory=dict)
    posthoc_target_score: float | None = None
    posthoc_target_breakdown: dict[str, float] = Field(default_factory=dict)


class BenchmarkSnapshotRecord(BaseModel):
    cutoff_date: date
    complaint_count_visible: int
    signal_count: int
    max_risk_score: float
    max_risk_signal_id: str | None = None
    distance_to_alert_threshold: float | None = None
    threshold_margin: float | None = None
    alert_signal_ids: tuple[str, ...] = Field(default_factory=tuple)
    alert_lineage_ids: tuple[str, ...] = Field(default_factory=tuple)
    top_candidates: tuple[BenchmarkCandidateRecord, ...] = Field(default_factory=tuple)


class BenchmarkCaseResult(BaseModel):
    case_id: str
    name: str
    expected_role: ExpectedRole
    benchmark_split: BenchmarkSplit
    vehicle: Vehicle
    campaign_number: str | None = None
    boundary_date: date
    adjudication_end_date: date | None = None
    selection_stratum: str | None = None
    status: str
    benchmark_valid: bool = True
    invalid_reason: str | None = None
    earliest_target_like_candidate_date: date | None = None
    first_any_alert_date: date | None = None
    first_alert_date: date | None = None
    last_alert_date: date | None = None
    first_qualified_alert_date: date | None = None
    lead_time_days: int | None = None
    alert_snapshot_count: int = 0
    alert_signal_occurrences: int = 0
    max_consecutive_alert_snapshots: int = 0
    max_alert_duration_days: int = 0
    max_risk_score: float = 0.0
    max_risk_date: date | None = None
    max_threshold_margin: float | None = None
    first_alert_risk_score: float | None = None
    first_alert_threshold_margin: float | None = None
    first_alert_evidence_count: int | None = None
    unique_alert_lineages: int = 0
    alert_lineages: dict[str, BenchmarkAlertLineageSummary] = Field(default_factory=dict)
    replay_days: int = 0
    replay_years: float = 0.0
    anti_leakage_checks: dict[str, bool] = Field(default_factory=dict)
    input_provenance: BenchmarkInputProvenance = Field(default_factory=BenchmarkInputProvenance)
    semantic_provenance: BenchmarkSemanticProvenance = Field(default_factory=BenchmarkSemanticProvenance)
    alert_evidence: dict[str, BenchmarkAlertEvidence] = Field(default_factory=dict)
    warnings: tuple[str, ...] = Field(default_factory=tuple)
    snapshots: tuple[BenchmarkSnapshotRecord, ...] = Field(default_factory=tuple)


class BenchmarkRunResult(BaseModel):
    benchmark_run_id: str
    benchmark_id: str
    benchmark_split: BenchmarkSplit
    freeze_id: str
    freeze_verified: bool
    lock_verified: bool
    manifest_path: str
    manifest_sha256: str
    lock_path: str | None = None
    freeze_manifest_path: str
    case_count: int
    cases: tuple[BenchmarkCaseResult, ...]
    aggregate: dict[str, Any]
    acceptance_criteria: BenchmarkAcceptanceCriteria
    metric_caveats: tuple[str, ...] = Field(default_factory=tuple)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BenchmarkPreflightCase(BaseModel):
    case_id: str
    name: str
    expected_role: ExpectedRole
    benchmark_split: BenchmarkSplit
    vehicle: Vehicle
    campaign_number: str | None = None
    expected_boundary_date: date
    observed_campaign_date: date | None = None
    campaign_found_for_vehicle: bool | None = None
    complaint_count_raw: int = 0
    complaint_count_eligible: int = 0
    recall_count_raw: int = 0
    complaint_adapter_revision: str | None = None
    complaint_models_requested: tuple[str, ...] = Field(default_factory=tuple)
    complaint_models_resolved: tuple[str, ...] = Field(default_factory=tuple)
    complaint_models_queried: tuple[str, ...] = Field(default_factory=tuple)
    complaint_count_by_model_variant: dict[str, int] = Field(default_factory=dict)
    valid: bool = True
    errors: tuple[str, ...] = Field(default_factory=tuple)
    warnings: tuple[str, ...] = Field(default_factory=tuple)


class BenchmarkPreflightResult(BaseModel):
    benchmark_id: str
    split: BenchmarkSplit
    freeze_id: str
    freeze_verified: bool
    manifest_sha256: str
    case_count: int
    positive_count: int
    control_count: int
    distinct_manufacturers: int
    distinct_selection_strata: int
    duplicate_case_ids: tuple[str, ...] = Field(default_factory=tuple)
    ready: bool
    cases: tuple[BenchmarkPreflightCase, ...]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))




def _complaint_query_provenance(raw_by_year: Any) -> dict[str, Any]:
    """Summarize NHTSA complaint model-resolution provenance from cached raw payloads."""

    if not isinstance(raw_by_year, dict):
        return {}
    revisions: list[str] = []
    requested: list[str] = []
    resolved: list[str] = []
    queried: list[str] = []
    counts: dict[str, int] = {}

    def _append_unique(target: list[str], value: Any) -> None:
        text = " ".join(str(value or "").strip().upper().split())
        if text and text not in target:
            target.append(text)

    for year, payload in sorted(raw_by_year.items(), key=lambda item: str(item[0])):
        if not isinstance(payload, dict):
            continue
        query = payload.get("recallzeroQuery") or {}
        if not isinstance(query, dict):
            continue
        revision = str(query.get("adapterRevision") or "").strip()
        if revision and revision not in revisions:
            revisions.append(revision)
        _append_unique(requested, query.get("requestedModel"))
        for value in query.get("catalogModelsResolved") or ():
            _append_unique(resolved, value)
        for value in query.get("queriedModels") or ():
            _append_unique(queried, value)
        raw_counts = query.get("countByModelVariant") or {}
        if isinstance(raw_counts, dict):
            for model, count in raw_counts.items():
                try:
                    counts[f"{year}:{str(model).strip().upper()}"] = int(count)
                except (TypeError, ValueError):
                    continue

    return {
        "adapter_revision": revisions[0] if len(revisions) == 1 else (",".join(revisions) or None),
        "requested": tuple(requested),
        "resolved": tuple(resolved),
        "queried": tuple(queried),
        "counts": counts,
    }


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _fingerprint_models(items: Sequence[BaseModel], *, sort_key) -> str:
    rows = [item.model_dump(mode="json") for item in sorted(items, key=sort_key)]
    return hashlib.sha256(_canonical_json(rows)).hexdigest()


def load_benchmark_manifest(path: Path) -> BenchmarkManifest:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if isinstance(raw, list):
        raw = {"candidates": raw}
    if not isinstance(raw, dict):
        raise ValueError("Benchmark manifest must be a YAML mapping")
    # Backward-compatible 0.3.4 manifests may contain only candidates.
    raw.setdefault("benchmark", {})
    return BenchmarkManifest.model_validate(raw)


def load_candidates(path: Path, *, split: BenchmarkSplit | None = None) -> list[BenchmarkCandidateConfig]:
    manifest = load_benchmark_manifest(path)
    rows = [item for item in manifest.candidates if item.enabled]
    if split is not None:
        rows = [item for item in rows if item.benchmark_split == split]
    return rows


def _duplicate_case_ids(cases: Sequence[BenchmarkCandidateConfig]) -> tuple[str, ...]:
    counts = Counter(item.case_id for item in cases)
    return tuple(sorted(case_id for case_id, count in counts.items() if count > 1))


def create_benchmark_lock(
    *,
    candidates_path: Path,
    freeze_manifest_path: Path,
    split: BenchmarkSplit,
) -> BenchmarkManifestLock:
    manifest = load_benchmark_manifest(candidates_path)
    freeze = load_freeze_manifest(freeze_manifest_path)
    cases = load_candidates(candidates_path, split=split)
    duplicates = _duplicate_case_ids(load_candidates(candidates_path))
    if duplicates:
        raise ValueError(f"Duplicate benchmark case_id values: {', '.join(duplicates)}")
    if not cases:
        raise ValueError(f"No enabled benchmark cases are registered for split '{split}'")
    return BenchmarkManifestLock(
        benchmark_id=manifest.benchmark.id,
        freeze_id=str(freeze["freeze_id"]),
        split=split,
        candidate_manifest_sha256=sha256_file(candidates_path),
        freeze_manifest_sha256=sha256_file(freeze_manifest_path),
        case_ids=tuple(item.case_id for item in cases),
        case_count=len(cases),
        positive_count=sum(item.expected_role == "positive" for item in cases),
        control_count=sum(item.expected_role == "negative" for item in cases),
    )


def verify_benchmark_lock(
    *,
    lock_path: Path,
    candidates_path: Path,
    freeze_manifest_path: Path,
    split: BenchmarkSplit,
) -> BenchmarkLockVerification:
    lock = BenchmarkManifestLock.model_validate_json(lock_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if lock.split != split:
        errors.append(f"lock split is {lock.split}, requested split is {split}")
    if lock.candidate_manifest_sha256 != sha256_file(candidates_path):
        errors.append("candidate manifest hash changed after lock creation")
    if lock.freeze_manifest_sha256 != sha256_file(freeze_manifest_path):
        errors.append("freeze manifest hash changed after lock creation")
    freeze_id = str(load_freeze_manifest(freeze_manifest_path)["freeze_id"])
    if lock.freeze_id != freeze_id:
        errors.append(f"freeze id changed from {lock.freeze_id} to {freeze_id}")
    current_ids = tuple(item.case_id for item in load_candidates(candidates_path, split=split))
    if lock.case_ids != current_ids:
        errors.append("selected case IDs/order changed after lock creation")
    return BenchmarkLockVerification(ok=not errors, errors=tuple(errors))


def _risk_factor_map(signal) -> dict[str, dict[str, Any]]:
    return {
        factor.name: {
            "score": factor.score,
            "weight": factor.weight,
            "contribution": factor.contribution,
            "explanation": factor.explanation,
        }
        for factor in signal.risk.factors
    }


def _snapshot_from_backtest(snapshot, *, alert_threshold: float) -> BenchmarkSnapshotRecord:
    return BenchmarkSnapshotRecord(
        cutoff_date=snapshot.cutoff_date,
        complaint_count_visible=snapshot.complaint_count_visible,
        signal_count=snapshot.signal_count,
        max_risk_score=snapshot.max_risk_score,
        max_risk_signal_id=snapshot.max_risk_signal_id,
        distance_to_alert_threshold=snapshot.distance_to_alert_threshold,
        threshold_margin=round(snapshot.max_risk_score - alert_threshold, 2),
        alert_signal_ids=tuple(signal.signal_id for signal in snapshot.alerts),
        alert_lineage_ids=tuple(signal.lineage_id for signal in snapshot.alerts),
        top_candidates=tuple(
            BenchmarkCandidateRecord(
                signal_id=item.signal_id,
                lineage_id=item.lineage_id,
                signal_scope=item.signal_scope,
                issue=item.issue,
                cluster_id=item.cluster_id,
                system=item.system,
                failure_mode=item.failure_mode,
                defect_family=item.defect_family,
                failure_mechanism=item.failure_mechanism,
                consequence_family=item.consequence_family,
                source_systems=item.source_systems,
                member_ids=item.member_ids,
                embedding_method=item.embedding_method.value,
                evidence_count=item.evidence_count,
                risk_score=item.risk_score,
                risk_factors={
                    name: value.model_dump(mode="python")
                    for name, value in item.risk_factors.items()
                },
                alert=item.alert,
                distance_to_alert_threshold=item.distance_to_alert_threshold,
                threshold_margin=round(item.risk_score - alert_threshold, 2),
                visible_recall_matched=item.visible_recall_matched,
                visible_recall_campaign_number=item.visible_recall_campaign_number,
                visible_recall_score=item.visible_recall_score,
                visible_recall_breakdown=item.visible_recall_breakdown,
                posthoc_target_score=item.posthoc_target_score,
                posthoc_target_breakdown=item.posthoc_target_breakdown,
            )
            for item in snapshot.top_candidates
        ),
    )


def _schedule(replay_start: date, boundary: date) -> list[date]:
    return RecallTimeMachine._cutoff_schedule(replay_start, boundary)


def _semantic_provenance(
    signatures: Sequence[FailureSignature], *, degraded_snapshot_count: int
) -> BenchmarkSemanticProvenance:
    counts = Counter(str(item.extraction_method.value) for item in signatures)
    total = len(signatures)
    nim_count = counts.get("nim", 0)
    return BenchmarkSemanticProvenance(
        signature_count=total,
        nim_count=nim_count,
        heuristic_count=counts.get("heuristic", 0),
        cached_count=counts.get("cached", 0),
        other_count=max(0, total - nim_count - counts.get("heuristic", 0) - counts.get("cached", 0)),
        nim_fraction=round(nim_count / total, 6) if total else None,
        degraded_snapshot_count=degraded_snapshot_count,
    )


def _input_provenance(
    *,
    complaints: Sequence[Complaint],
    recalls: Sequence[Recall],
    signatures: Sequence[FailureSignature],
    boundary: date,
) -> BenchmarkInputProvenance:
    eligible = [item for item in complaints if item.received_date < boundary]
    return BenchmarkInputProvenance(
        complaint_count_raw=len(complaints),
        complaint_count_eligible=len(eligible),
        recall_count_raw=len(recalls),
        complaint_dataset_sha256=_fingerprint_models(complaints, sort_key=lambda item: item.odi_number),
        eligible_complaint_dataset_sha256=_fingerprint_models(eligible, sort_key=lambda item: item.odi_number),
        recall_dataset_sha256=_fingerprint_models(recalls, sort_key=lambda item: item.campaign_number),
        signature_cache_sha256=(
            _fingerprint_models(signatures, sort_key=lambda item: item.complaint_id) if signatures else None
        ),
    )


def _alert_lineage_summaries(
    snapshots: Sequence[BenchmarkSnapshotRecord],
) -> dict[str, BenchmarkAlertLineageSummary]:
    indexes: dict[str, list[int]] = defaultdict(list)
    dates: dict[str, list[date]] = defaultdict(list)
    for index, snapshot in enumerate(snapshots):
        for lineage in set(snapshot.alert_lineage_ids):
            indexes[lineage].append(index)
            dates[lineage].append(snapshot.cutoff_date)

    result: dict[str, BenchmarkAlertLineageSummary] = {}
    for lineage, positions in indexes.items():
        best_count = 0
        best_start = 0
        best_end = 0
        run_start = 0
        for offset in range(len(positions)):
            if offset == 0 or positions[offset] != positions[offset - 1] + 1:
                run_start = offset
            count = offset - run_start + 1
            if count > best_count:
                best_count = count
                best_start = run_start
                best_end = offset
        lineage_dates = dates[lineage]
        duration = (lineage_dates[best_end] - lineage_dates[best_start]).days if lineage_dates else 0
        result[lineage] = BenchmarkAlertLineageSummary(
            lineage_id=lineage,
            first_date=min(lineage_dates),
            last_date=max(lineage_dates),
            snapshot_count=len(positions),
            max_consecutive_snapshots=best_count,
            max_consecutive_duration_days=max(0, duration),
        )
    return result


def _case_alert_metrics(
    snapshots: Sequence[BenchmarkSnapshotRecord],
) -> dict[str, Any]:
    alert_snapshots = [item for item in snapshots if item.alert_signal_ids]
    lineages = _alert_lineage_summaries(snapshots)
    first_candidate: BenchmarkCandidateRecord | None = None
    if alert_snapshots:
        first_snapshot = alert_snapshots[0]
        alert_candidates = [item for item in first_snapshot.top_candidates if item.alert]
        if alert_candidates:
            first_candidate = max(alert_candidates, key=lambda item: item.risk_score)
    return {
        "first_alert_date": alert_snapshots[0].cutoff_date if alert_snapshots else None,
        "last_alert_date": alert_snapshots[-1].cutoff_date if alert_snapshots else None,
        "max_consecutive_alert_snapshots": max(
            (item.max_consecutive_snapshots for item in lineages.values()), default=0
        ),
        "max_alert_duration_days": max(
            (item.max_consecutive_duration_days for item in lineages.values()), default=0
        ),
        "alert_lineages": lineages,
        "unique_alert_lineages": len(lineages),
        "first_alert_risk_score": first_candidate.risk_score if first_candidate else None,
        "first_alert_threshold_margin": first_candidate.threshold_margin if first_candidate else None,
        "first_alert_evidence_count": first_candidate.evidence_count if first_candidate else None,
        "max_threshold_margin": max(
            (item.threshold_margin for item in snapshots if item.threshold_margin is not None), default=None
        ),
    }


def _collect_alert_evidence(
    *,
    snapshots: Sequence[BenchmarkSnapshotRecord],
    complaints: Sequence[Complaint],
    signatures: Sequence[FailureSignature],
) -> dict[str, BenchmarkAlertEvidence]:
    needed = {
        member_id
        for snapshot in snapshots
        for candidate in snapshot.top_candidates
        if candidate.alert
        for member_id in candidate.member_ids
    }
    complaint_by_id = {item.odi_number: item for item in complaints}
    signature_by_id = {item.complaint_id: item for item in signatures}
    result: dict[str, BenchmarkAlertEvidence] = {}
    for complaint_id in sorted(needed):
        complaint = complaint_by_id.get(complaint_id)
        signature = signature_by_id.get(complaint_id)
        if complaint is not None and signature is not None:
            result[complaint_id] = BenchmarkAlertEvidence(complaint=complaint, signature=signature)
    return result


def _validity(
    *,
    freeze_ok: bool,
    anti_leakage_checks: dict[str, bool],
    semantic: BenchmarkSemanticProvenance,
    minimum_nim_fraction: float,
) -> tuple[bool, str | None]:
    if not freeze_ok:
        return False, "FREEZE_MISMATCH"
    if anti_leakage_checks and not all(anti_leakage_checks.values()):
        return False, "ANTI_LEAKAGE_FAILURE"
    if semantic.degraded_snapshot_count:
        return False, "SEMANTIC_QUALITY_DEGRADED"
    if (
        semantic.signature_count > 0
        and semantic.nim_fraction is not None
        and semantic.nim_fraction < minimum_nim_fraction
    ):
        return False, "NIM_EXTRACTION_FAILURE"
    return True, None


def _invalid_case_from_exception(
    case: BenchmarkCandidateConfig,
    exc: Exception,
    *,
    freeze_ok: bool,
) -> BenchmarkCaseResult:
    message = str(exc)
    lowered = message.lower()
    if "campaign" in lowered and ("not" in lowered or "could not" in lowered):
        reason = "CAMPAIGN_NOT_FOUND"
    elif "nim" in lowered or "extraction" in lowered:
        reason = "NIM_EXTRACTION_FAILURE"
    else:
        reason = "INPUT_DATA_ERROR"
    if not freeze_ok:
        reason = "FREEZE_MISMATCH"
    return BenchmarkCaseResult(
        case_id=case.case_id,
        name=case.name,
        expected_role=case.expected_role,
        benchmark_split=case.benchmark_split,
        vehicle=case.vehicle,
        campaign_number=case.campaign_number,
        boundary_date=case.boundary_date,
        adjudication_end_date=case.adjudication_end_date,
        selection_stratum=case.selection_stratum,
        status="INVALID_CASE",
        benchmark_valid=False,
        invalid_reason=reason,
        warnings=(f"Benchmark case failed before a valid detector outcome could be recorded: {message}",),
    )


async def _run_negative_control(
    *,
    pipeline: RecallZeroPipeline,
    case: BenchmarkCandidateConfig,
    complaints: Sequence[Complaint],
    recalls: Sequence[Recall],
    use_signature_cache: bool,
    top_candidate_count: int,
    freeze_ok: bool,
    minimum_nim_fraction: float,
) -> BenchmarkCaseResult:
    boundary = case.boundary_date
    pre_boundary = sorted(
        (item for item in complaints if item.received_date < boundary),
        key=lambda item: (item.received_date, item.odi_number),
    )
    one_year_before = boundary - timedelta(days=365)
    earliest = min((item.received_date for item in pre_boundary), default=one_year_before)
    replay_start = max(earliest, one_year_before)

    signatures: list[FailureSignature] = []
    if pre_boundary:
        signatures = await pipeline.extract_signatures(
            case.vehicle, pre_boundary, use_cache=use_signature_cache
        )
    signature_by_id = {item.complaint_id: item for item in signatures}

    snapshots: list[BenchmarkSnapshotRecord] = []
    alert_occurrences = 0
    degraded_count = 0
    threshold = pipeline.risk_config.alert_threshold

    for cutoff in _schedule(replay_start, boundary):
        visible = [item for item in pre_boundary if item.received_date <= cutoff]
        visible_signatures = [
            signature_by_id[item.odi_number]
            for item in visible
            if item.odi_number in signature_by_id
        ]
        run = await pipeline.analyze_records(
            vehicle=case.vehicle,
            complaints=visible,
            recalls=recalls,
            cutoff_date=cutoff,
            signatures=visible_signatures,
            risk_config=pipeline.risk_config,
            save=False,
        )
        degraded = run.semantic_quality == "DEGRADED"
        if degraded:
            degraded_count += 1
        alerts = [] if degraded else [signal for signal in run.signals if signal.risk.alert]
        alert_occurrences += len(alerts)

        ranked = list(
            sorted(run.signals, key=lambda item: (-item.risk.final_score, item.cluster.label))[
                : max(1, top_candidate_count)
            ]
        )
        ranked_ids = {item.signal_id for item in ranked}
        ranked.extend(signal for signal in alerts if signal.signal_id not in ranked_ids)
        max_signal = ranked[0] if ranked else None
        snapshots.append(
            BenchmarkSnapshotRecord(
                cutoff_date=cutoff,
                complaint_count_visible=len(visible),
                signal_count=len(run.signals),
                max_risk_score=max_signal.risk.final_score if max_signal else 0.0,
                max_risk_signal_id=max_signal.signal_id if max_signal else None,
                distance_to_alert_threshold=(
                    round(max(0.0, threshold - max_signal.risk.final_score), 2)
                    if max_signal
                    else threshold
                ),
                threshold_margin=(
                    round(max_signal.risk.final_score - threshold, 2) if max_signal else round(-threshold, 2)
                ),
                alert_signal_ids=tuple(signal.signal_id for signal in alerts),
                alert_lineage_ids=tuple(signal.lineage_id for signal in alerts),
                top_candidates=tuple(
                    BenchmarkCandidateRecord(
                        signal_id=signal.signal_id,
                        lineage_id=signal.lineage_id,
                        signal_scope=signal.signal_scope,
                        issue=signal.cluster.label,
                        cluster_id=signal.cluster.cluster_id,
                        system=signal.cluster.system,
                        failure_mode=signal.cluster.failure_mode,
                        defect_family=signal.cluster.defect_family,
                        failure_mechanism=signal.cluster.failure_mechanism,
                        consequence_family=signal.cluster.consequence_family,
                        source_systems=signal.cluster.source_systems,
                        member_ids=signal.cluster.member_ids,
                        embedding_method=signal.cluster.embedding_method.value,
                        evidence_count=signal.cluster.evidence_count,
                        risk_score=signal.risk.final_score,
                        risk_factors=_risk_factor_map(signal),
                        alert=signal.risk.alert and not degraded,
                        distance_to_alert_threshold=round(
                            max(0.0, threshold - signal.risk.final_score), 2
                        ),
                        threshold_margin=round(signal.risk.final_score - threshold, 2),
                        visible_recall_matched=signal.recall_match.matched,
                        visible_recall_campaign_number=signal.recall_match.campaign_number,
                        visible_recall_score=signal.recall_match.score,
                        visible_recall_breakdown=signal.recall_match.score_breakdown,
                    )
                    for signal in ranked
                ),
            )
        )

    alert_snapshots = [item for item in snapshots if item.alert_signal_ids]
    first_any = alert_snapshots[0].cutoff_date if alert_snapshots else None
    max_snapshot = max(snapshots, key=lambda item: item.max_risk_score, default=None)
    replay_days = max(0, (boundary - replay_start).days)
    warnings: list[str] = []
    if degraded_count:
        warnings.append(
            f"Withheld alerts from {degraded_count} control snapshot(s) because semantic/clustering quality was DEGRADED."
        )

    checks = {
        "complaints_strictly_before_boundary": all(item.received_date < boundary for item in pre_boundary),
        "snapshots_strictly_before_boundary": all(item.cutoff_date < boundary for item in snapshots),
        "no_target_recall_used": True,
    }
    semantic = _semantic_provenance(signatures, degraded_snapshot_count=degraded_count)
    valid, invalid_reason = _validity(
        freeze_ok=freeze_ok,
        anti_leakage_checks=checks,
        semantic=semantic,
        minimum_nim_fraction=minimum_nim_fraction,
    )
    status = "CONTROL_ALERT_REQUIRES_ADJUDICATION" if first_any else "CONTROL_QUIET"
    if not valid:
        status = "INVALID_CONTROL_REPLAY"

    alert_metrics = _case_alert_metrics(snapshots)
    return BenchmarkCaseResult(
        case_id=case.case_id,
        name=case.name,
        expected_role=case.expected_role,
        benchmark_split=case.benchmark_split,
        vehicle=case.vehicle,
        campaign_number=None,
        boundary_date=boundary,
        adjudication_end_date=case.adjudication_end_date,
        selection_stratum=case.selection_stratum,
        status=status,
        benchmark_valid=valid,
        invalid_reason=invalid_reason,
        first_any_alert_date=first_any,
        alert_snapshot_count=len(alert_snapshots),
        alert_signal_occurrences=alert_occurrences,
        max_risk_score=max_snapshot.max_risk_score if max_snapshot else 0.0,
        max_risk_date=max_snapshot.cutoff_date if max_snapshot else None,
        replay_days=replay_days,
        replay_years=round(replay_days / 365.25, 4),
        anti_leakage_checks=checks,
        input_provenance=_input_provenance(
            complaints=complaints, recalls=recalls, signatures=signatures, boundary=boundary
        ),
        semantic_provenance=semantic,
        alert_evidence=_collect_alert_evidence(
            snapshots=snapshots, complaints=pre_boundary, signatures=signatures
        ),
        warnings=tuple(warnings),
        snapshots=tuple(snapshots),
        **alert_metrics,
    )




def _enforce_strict_nim_validation(pipeline: RecallZeroPipeline, minimum_nim_fraction: float) -> None:
    """Fail closed on semantic extraction errors for strict Detector v1 validation.

    The benchmark already requires a 1.0 NIM fraction. Allowing a structured NIM
    failure to fall through to the heuristic only wastes the rest of the case and
    creates a MIXED semantic artifact that is invalid anyway. This helper changes
    no detector math; it only makes the benchmark fail at the point semantic
    consistency is lost.
    """

    if minimum_nim_fraction < 1.0:
        return
    extractor = getattr(pipeline, "extractor", None)
    if extractor is None:
        return
    if hasattr(extractor, "fallback_on_error"):
        extractor.fallback_on_error = False
    if hasattr(extractor, "fallback_on_transient_error"):
        extractor.fallback_on_transient_error = False

async def run_benchmark(
    *,
    settings: Settings,
    candidates_path: Path,
    freeze_manifest_path: Path,
    split: BenchmarkSplit = "development",
    lock_path: Path | None = None,
    refresh: bool = False,
    allow_freeze_mismatch: bool = False,
    confirm_holdout: bool = False,
    pipeline_factory=build_pipeline,
) -> tuple[BenchmarkRunResult, FreezeVerification]:
    if split == "holdout" and not confirm_holdout:
        raise RuntimeError("Holdout execution requires explicit confirmation")
    if split in {"validation", "holdout"} and lock_path is None:
        raise RuntimeError(f"A benchmark lock is required for split '{split}'")

    verification = verify_freeze(freeze_manifest_path, settings)
    if not verification.ok and not allow_freeze_mismatch:
        details = "; ".join(
            f"{item.name}: expected {item.expected}, actual {item.actual}"
            for item in verification.failures[:6]
        )
        raise RuntimeError(f"Detector freeze mismatch; benchmark comparability is invalid. {details}")

    lock_verified = False
    if lock_path is not None:
        lock_verification = verify_benchmark_lock(
            lock_path=lock_path,
            candidates_path=candidates_path,
            freeze_manifest_path=freeze_manifest_path,
            split=split,
        )
        if not lock_verification.ok:
            raise RuntimeError("Benchmark manifest lock mismatch: " + "; ".join(lock_verification.errors))
        lock_verified = True

    manifest = load_freeze_manifest(freeze_manifest_path)
    benchmark_manifest = load_benchmark_manifest(candidates_path)
    evaluation = manifest.get("evaluation", {}) or {}
    target_match_threshold = float(evaluation.get("target_match_threshold", 0.45))
    top_candidate_count = int(evaluation.get("top_candidate_count", 5))
    minimum_nim_fraction = benchmark_manifest.benchmark.acceptance_criteria.minimum_nim_fraction

    selected = load_candidates(candidates_path, split=split)
    duplicates = _duplicate_case_ids(load_candidates(candidates_path))
    if duplicates:
        raise RuntimeError(f"Duplicate benchmark case_id values: {', '.join(duplicates)}")
    if not selected:
        raise RuntimeError(f"No enabled benchmark cases are registered for split '{split}'")

    cases: list[BenchmarkCaseResult] = []
    for case in selected:
        try:
            pipeline = pipeline_factory(settings, use_nim=settings.use_nim)
            _enforce_strict_nim_validation(pipeline, minimum_nim_fraction)
            complaints, recalls = await pipeline.ingest(case.vehicle, refresh=refresh)

            if case.expected_role == "positive":
                assert case.campaign_number is not None
                assert case.official_recall_date is not None
                clean_campaign = case.campaign_number.upper().replace("-", "")
                target = next((item for item in recalls if item.campaign_number == clean_campaign), None)
                if target is None:
                    target = await pipeline.nhtsa.fetch_campaign(clean_campaign)
                if target is None:
                    raise RuntimeError(
                        f"Campaign {clean_campaign} could not be retrieved for case {case.name}"
                    )

                result = await RecallTimeMachine(
                    pipeline,
                    target_match_threshold=target_match_threshold,
                    top_candidate_count=top_candidate_count,
                ).run(
                    vehicle=case.vehicle,
                    complaints=complaints,
                    recalls=recalls,
                    target_recall=target,
                    official_recall_date=case.official_recall_date,
                    use_signature_cache=not refresh,
                    save=False,
                )
                threshold = pipeline.risk_config.alert_threshold
                snapshots = tuple(
                    _snapshot_from_backtest(item, alert_threshold=threshold) for item in result.snapshots
                )
                max_snapshot = max(snapshots, key=lambda item: item.max_risk_score, default=None)
                alert_occurrences = sum(len(snapshot.alert_signal_ids) for snapshot in snapshots)
                replay_days = (
                    (case.boundary_date - snapshots[0].cutoff_date).days if snapshots else 0
                )
                eligible_ids = {
                    item.odi_number for item in complaints if item.received_date < case.boundary_date
                }
                cached = pipeline.repository.load_signatures(case.vehicle)
                signatures = [
                    item for complaint_id, item in cached.items() if complaint_id in eligible_ids
                ]
                semantic = _semantic_provenance(
                    signatures, degraded_snapshot_count=result.degraded_snapshot_count
                )
                valid, invalid_reason = _validity(
                    freeze_ok=verification.ok,
                    anti_leakage_checks=result.anti_leakage_checks,
                    semantic=semantic,
                    minimum_nim_fraction=minimum_nim_fraction,
                )
                alert_metrics = _case_alert_metrics(snapshots)
                cases.append(
                    BenchmarkCaseResult(
                        case_id=case.case_id,
                        name=case.name,
                        expected_role=case.expected_role,
                        benchmark_split=case.benchmark_split,
                        vehicle=case.vehicle,
                        campaign_number=clean_campaign,
                        boundary_date=case.boundary_date,
                        selection_stratum=case.selection_stratum,
                        status=result.status if valid else "INVALID_BACKTEST",
                        benchmark_valid=valid,
                        invalid_reason=invalid_reason,
                        earliest_target_like_candidate_date=result.earliest_target_like_candidate_date,
                        first_any_alert_date=result.first_any_alert_date,
                        first_qualified_alert_date=result.first_qualified_alert_date,
                        lead_time_days=result.lead_time_days,
                        alert_snapshot_count=result.alert_snapshot_count,
                        alert_signal_occurrences=alert_occurrences,
                        max_risk_score=max_snapshot.max_risk_score if max_snapshot else 0.0,
                        max_risk_date=max_snapshot.cutoff_date if max_snapshot else None,
                        replay_days=replay_days,
                        replay_years=round(replay_days / 365.25, 4),
                        anti_leakage_checks=result.anti_leakage_checks,
                        input_provenance=_input_provenance(
                            complaints=complaints,
                            recalls=recalls,
                            signatures=signatures,
                            boundary=case.boundary_date,
                        ),
                        semantic_provenance=semantic,
                        alert_evidence=_collect_alert_evidence(
                            snapshots=snapshots,
                            complaints=[
                                item for item in complaints if item.received_date < case.boundary_date
                            ],
                            signatures=signatures,
                        ),
                        warnings=result.warnings,
                        snapshots=snapshots,
                        **alert_metrics,
                    )
                )
            else:
                cases.append(
                    await _run_negative_control(
                        pipeline=pipeline,
                        case=case,
                        complaints=complaints,
                        recalls=recalls,
                        use_signature_cache=not refresh,
                        top_candidate_count=top_candidate_count,
                        freeze_ok=verification.ok,
                        minimum_nim_fraction=minimum_nim_fraction,
                    )
                )
        except Exception as exc:  # Preserve other cases; mark infrastructure failure explicitly.
            cases.append(_invalid_case_from_exception(case, exc, freeze_ok=verification.ok))

    aggregate = aggregate_benchmark(cases)
    run_id = stable_id(
        "bench",
        manifest.get("freeze_id", "unknown"),
        benchmark_manifest.benchmark.id,
        split,
        *(case.case_id for case in cases),
    )
    result = BenchmarkRunResult(
        benchmark_run_id=run_id,
        benchmark_id=benchmark_manifest.benchmark.id,
        benchmark_split=split,
        freeze_id=str(manifest.get("freeze_id", "unknown")),
        freeze_verified=verification.ok,
        lock_verified=lock_verified,
        manifest_path=str(candidates_path),
        manifest_sha256=sha256_file(candidates_path),
        lock_path=str(lock_path) if lock_path else None,
        freeze_manifest_path=str(freeze_manifest_path),
        case_count=len(cases),
        cases=tuple(cases),
        aggregate=aggregate,
        acceptance_criteria=benchmark_manifest.benchmark.acceptance_criteria,
        metric_caveats=(
            "Control alerts in this raw artifact are not false positives until post-hoc adjudication is complete.",
            "unique alert lineage counts can be inflated by TAX-001 taxonomy-driven lineage fragmentation; inspect evidence overlap before reading lineage count as distinct detector failures.",
        ),
    )
    return result, verification


async def preflight_benchmark(
    *,
    settings: Settings,
    candidates_path: Path,
    freeze_manifest_path: Path,
    split: BenchmarkSplit,
    refresh: bool = False,
    pipeline_factory=build_pipeline,
) -> BenchmarkPreflightResult:
    """Validate benchmark identity/data prerequisites without running extraction or detection."""

    verification = verify_freeze(freeze_manifest_path, settings)
    freeze_manifest = load_freeze_manifest(freeze_manifest_path)
    expected_complaint_adapter = str(
        freeze_manifest.get("input_adapter_revision") or "nhtsa-complaint-catalog-v2"
    )
    manifest = load_benchmark_manifest(candidates_path)
    all_cases = load_candidates(candidates_path)
    selected = [item for item in all_cases if item.benchmark_split == split]
    duplicates = _duplicate_case_ids(all_cases)
    rows: list[BenchmarkPreflightCase] = []

    for case in selected:
        errors: list[str] = []
        warnings: list[str] = []
        observed_date: date | None = None
        campaign_found: bool | None = None
        complaint_count_raw = 0
        complaint_count_eligible = 0
        recall_count_raw = 0
        complaint_query: dict[str, Any] = {}
        try:
            pipeline = pipeline_factory(settings, use_nim=False)
            complaints, recalls = await pipeline.ingest(case.vehicle, refresh=refresh)
            complaint_count_raw = len(complaints)
            complaint_count_eligible = sum(
                item.received_date < case.boundary_date for item in complaints
            )
            recall_count_raw = len(recalls)
            repository = getattr(pipeline, "repository", None)
            if repository is not None and hasattr(repository, "load_raw"):
                complaint_query = _complaint_query_provenance(
                    repository.load_raw(case.vehicle, "complaints")
                )
                adapter_revision = complaint_query.get("adapter_revision")
                if adapter_revision != expected_complaint_adapter:
                    errors.append(
                        f"complaint input lacks {expected_complaint_adapter} provenance; "
                        "rerun preflight with --refresh"
                    )
            if case.expected_role == "positive":
                assert case.campaign_number is not None
                clean = case.campaign_number.upper().replace("-", "")
                target = next((item for item in recalls if item.campaign_number == clean), None)
                campaign_found = target is not None
                if target is None:
                    fetched = await pipeline.nhtsa.fetch_campaign(clean)
                    if fetched is None:
                        errors.append(f"campaign {clean} could not be retrieved")
                    else:
                        observed_date = fetched.report_received_date
                        errors.append(
                            f"campaign {clean} exists but was not returned for the configured vehicle/year"
                        )
                else:
                    observed_date = target.report_received_date
                if observed_date is None:
                    errors.append(f"campaign {clean} has no report_received_date")
                elif observed_date != case.official_recall_date:
                    errors.append(
                        f"campaign date mismatch: manifest={case.official_recall_date} nhtsa={observed_date}"
                    )
            if complaint_count_eligible == 0:
                warnings.append("no complaints are eligible before the evaluation boundary")
            if (
                case.minimum_eligible_complaints is not None
                and complaint_count_eligible < case.minimum_eligible_complaints
            ):
                errors.append(
                    f"eligible complaint count {complaint_count_eligible} is below preregistered minimum {case.minimum_eligible_complaints}"
                )
        except Exception as exc:
            errors.append(f"NHTSA/vehicle data preflight failed: {exc}")

        if case.case_id in duplicates:
            errors.append("duplicate case_id")
        rows.append(
            BenchmarkPreflightCase(
                case_id=case.case_id,
                name=case.name,
                expected_role=case.expected_role,
                benchmark_split=case.benchmark_split,
                vehicle=case.vehicle,
                campaign_number=case.campaign_number,
                expected_boundary_date=case.boundary_date,
                observed_campaign_date=observed_date,
                campaign_found_for_vehicle=campaign_found,
                complaint_count_raw=complaint_count_raw,
                complaint_count_eligible=complaint_count_eligible,
                recall_count_raw=recall_count_raw,
                complaint_adapter_revision=complaint_query.get("adapter_revision"),
                complaint_models_requested=tuple(complaint_query.get("requested") or ()),
                complaint_models_resolved=tuple(complaint_query.get("resolved") or ()),
                complaint_models_queried=tuple(complaint_query.get("queried") or ()),
                complaint_count_by_model_variant=dict(complaint_query.get("counts") or {}),
                valid=not errors,
                errors=tuple(errors),
                warnings=tuple(warnings),
            )
        )

    ready = verification.ok and bool(rows) and not duplicates and all(item.valid for item in rows)
    return BenchmarkPreflightResult(
        benchmark_id=manifest.benchmark.id,
        split=split,
        freeze_id=verification.freeze_id,
        freeze_verified=verification.ok,
        manifest_sha256=sha256_file(candidates_path),
        case_count=len(rows),
        positive_count=sum(item.expected_role == "positive" for item in selected),
        control_count=sum(item.expected_role == "negative" for item in selected),
        distinct_manufacturers=len({item.vehicle.make for item in selected}),
        distinct_selection_strata=len(
            {item.selection_stratum for item in selected if item.selection_stratum}
        ),
        duplicate_case_ids=duplicates,
        ready=ready,
        cases=tuple(rows),
    )


def wilson_interval(successes: int, total: int, *, z: float = 1.959963984540054) -> dict[str, float] | None:
    if total <= 0:
        return None
    p = successes / total
    z2 = z * z
    denominator = 1.0 + z2 / total
    center = (p + z2 / (2.0 * total)) / denominator
    half = z * math.sqrt((p * (1.0 - p) / total) + z2 / (4.0 * total * total)) / denominator
    return {"low": round(max(0.0, center - half), 4), "high": round(min(1.0, center + half), 4)}


def aggregate_benchmark(cases: Sequence[BenchmarkCaseResult]) -> dict[str, Any]:
    valid = [item for item in cases if item.benchmark_valid]
    positives = [item for item in valid if item.expected_role == "positive"]
    negatives = [item for item in valid if item.expected_role == "negative"]
    qualified = [item for item in positives if item.first_qualified_alert_date is not None]
    lead_times = [item.lead_time_days for item in qualified if item.lead_time_days is not None]
    target_like_not_alerted = [
        item
        for item in positives
        if item.earliest_target_like_candidate_date is not None
        and item.first_qualified_alert_date is None
    ]
    control_alert_cases = [item for item in negatives if item.first_any_alert_date is not None]
    replay_years = sum(item.replay_years for item in negatives)
    alert_snapshots = sum(item.alert_snapshot_count for item in negatives)
    alert_lineages = sum(item.unique_alert_lineages for item in negatives)
    first_margins = [
        item.first_alert_threshold_margin
        for item in valid
        if item.first_alert_threshold_margin is not None
    ]
    first_evidence = [
        item.first_alert_evidence_count for item in valid if item.first_alert_evidence_count is not None
    ]
    persistence = [
        item.max_consecutive_alert_snapshots for item in valid if item.first_any_alert_date is not None
    ]

    return {
        "case_count": len(cases),
        "valid_case_count": len(valid),
        "invalid_case_count": len(cases) - len(valid),
        "valid_positive_count": len(positives),
        "valid_control_count": len(negatives),
        "positive_case_count": len(positives),
        "positive_qualified_count": len(qualified),
        "positive_sensitivity": round(len(qualified) / len(positives), 4) if positives else None,
        "positive_sensitivity_95ci": wilson_interval(len(qualified), len(positives)),
        "lead_time_days": lead_times,
        "lead_time_min": min(lead_times) if lead_times else None,
        "lead_time_median": median(lead_times) if lead_times else None,
        "lead_time_mean": round(mean(lead_times), 2) if lead_times else None,
        "lead_time_max": max(lead_times) if lead_times else None,
        "median_lead_time_days": median(lead_times) if lead_times else None,
        "target_like_but_not_alerted_count": len(target_like_not_alerted),
        "negative_case_count": len(negatives),
        "control_cases_with_alerts": len(control_alert_cases),
        "control_case_alert_rate": (
            round(len(control_alert_cases) / len(negatives), 4) if negatives else None
        ),
        "control_case_alert_rate_95ci": wilson_interval(len(control_alert_cases), len(negatives)),
        "negative_replay_years": round(replay_years, 4),
        "raw_control_alert_snapshots": alert_snapshots,
        "raw_control_alert_lineages": alert_lineages,
        "raw_control_alert_snapshots_per_vehicle_replay_year": (
            round(alert_snapshots / replay_years, 4) if replay_years else None
        ),
        "raw_control_alert_lineages_per_vehicle_replay_year": (
            round(alert_lineages / replay_years, 4) if replay_years else None
        ),
        "median_threshold_margin_at_first_alert": median(first_margins) if first_margins else None,
        "median_evidence_at_first_alert": median(first_evidence) if first_evidence else None,
        "median_alert_persistence_snapshots": median(persistence) if persistence else None,
        "all_valid_cases_anti_leakage_pass": all(
            all(item.anti_leakage_checks.values()) for item in valid if item.anti_leakage_checks
        ),
        "note": "Control-alert burden is raw/pre-adjudication. Use benchmark-adjudicate + benchmark-report before calling any control alert unconfirmed.",
    }


def write_benchmark_csv(result: BenchmarkRunResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "case_id",
        "name",
        "expected_role",
        "benchmark_split",
        "benchmark_valid",
        "invalid_reason",
        "make",
        "model",
        "model_years",
        "campaign_number",
        "boundary_date",
        "status",
        "earliest_target_like_candidate_date",
        "first_any_alert_date",
        "first_alert_date",
        "last_alert_date",
        "max_consecutive_alert_snapshots",
        "first_qualified_alert_date",
        "lead_time_days",
        "alert_snapshot_count",
        "alert_signal_occurrences",
        "max_risk_score",
        "max_risk_date",
        "max_threshold_margin",
        "first_alert_risk_score",
        "first_alert_threshold_margin",
        "first_alert_evidence_count",
        "unique_alert_lineages",
        "replay_years",
        "complaint_count_eligible",
        "nim_signature_fraction",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in result.cases:
            writer.writerow(
                {
                    "case_id": item.case_id,
                    "name": item.name,
                    "expected_role": item.expected_role,
                    "benchmark_split": item.benchmark_split,
                    "benchmark_valid": item.benchmark_valid,
                    "invalid_reason": item.invalid_reason or "",
                    "make": item.vehicle.make,
                    "model": item.vehicle.model,
                    "model_years": ",".join(str(year) for year in item.vehicle.model_years),
                    "campaign_number": item.campaign_number or "",
                    "boundary_date": item.boundary_date.isoformat(),
                    "status": item.status,
                    "earliest_target_like_candidate_date": item.earliest_target_like_candidate_date or "",
                    "first_any_alert_date": item.first_any_alert_date or "",
                    "first_alert_date": item.first_alert_date or "",
                    "last_alert_date": item.last_alert_date or "",
                    "max_consecutive_alert_snapshots": item.max_consecutive_alert_snapshots,
                    "first_qualified_alert_date": item.first_qualified_alert_date or "",
                    "lead_time_days": item.lead_time_days if item.lead_time_days is not None else "",
                    "alert_snapshot_count": item.alert_snapshot_count,
                    "alert_signal_occurrences": item.alert_signal_occurrences,
                    "max_risk_score": item.max_risk_score,
                    "max_risk_date": item.max_risk_date or "",
                    "max_threshold_margin": item.max_threshold_margin if item.max_threshold_margin is not None else "",
                    "first_alert_risk_score": item.first_alert_risk_score if item.first_alert_risk_score is not None else "",
                    "first_alert_threshold_margin": item.first_alert_threshold_margin if item.first_alert_threshold_margin is not None else "",
                    "first_alert_evidence_count": item.first_alert_evidence_count if item.first_alert_evidence_count is not None else "",
                    "unique_alert_lineages": item.unique_alert_lineages,
                    "replay_years": item.replay_years,
                    "complaint_count_eligible": item.input_provenance.complaint_count_eligible,
                    "nim_signature_fraction": item.semantic_provenance.nim_fraction if item.semantic_provenance.nim_fraction is not None else "",
                }
            )


def _candidate_map(case: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for snapshot in case.get("snapshots", []):
        cutoff = str(snapshot.get("cutoff_date"))
        for candidate in snapshot.get("top_candidates", []) or []:
            lineage = str(candidate.get("lineage_id") or candidate.get("signal_id"))
            result[(cutoff, lineage)] = candidate
    return result


def _lineage_structure_changes(left_case: dict[str, Any], right_case: dict[str, Any]) -> list[dict[str, Any]]:
    """Best-effort split/merge diagnostics from member-ID overlap at the same cutoff."""
    left_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    right_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for snapshot in left_case.get("snapshots", []):
        left_by_date[str(snapshot.get("cutoff_date"))].extend(snapshot.get("top_candidates", []) or [])
    for snapshot in right_case.get("snapshots", []):
        right_by_date[str(snapshot.get("cutoff_date"))].extend(snapshot.get("top_candidates", []) or [])
    changes: list[dict[str, Any]] = []
    for cutoff in sorted(set(left_by_date) & set(right_by_date)):
        overlaps: dict[str, list[tuple[str, float]]] = defaultdict(list)
        reverse: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for a in left_by_date[cutoff]:
            a_ids = set(a.get("member_ids") or [])
            if not a_ids:
                continue
            for b in right_by_date[cutoff]:
                b_ids = set(b.get("member_ids") or [])
                if not b_ids:
                    continue
                union = a_ids | b_ids
                score = len(a_ids & b_ids) / len(union) if union else 0.0
                if score >= 0.50 and a.get("lineage_id") != b.get("lineage_id"):
                    overlaps[str(a.get("lineage_id"))].append((str(b.get("lineage_id")), score))
                    reverse[str(b.get("lineage_id"))].append((str(a.get("lineage_id")), score))
        for lineage, targets in overlaps.items():
            unique_targets = sorted({item[0] for item in targets})
            if len(unique_targets) > 1:
                changes.append({"cutoff_date": cutoff, "type": "fragmented", "left_lineage": lineage, "right_lineages": unique_targets})
        for lineage, sources in reverse.items():
            unique_sources = sorted({item[0] for item in sources})
            if len(unique_sources) > 1:
                changes.append({"cutoff_date": cutoff, "type": "merged", "left_lineages": unique_sources, "right_lineage": lineage})
    return changes


def compare_benchmark_runs(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_cases = {item["case_id"]: item for item in left.get("cases", [])}
    right_cases = {item["case_id"]: item for item in right.get("cases", [])}
    rows: list[dict[str, Any]] = []
    for case_id in sorted(set(left_cases) | set(right_cases)):
        a = left_cases.get(case_id)
        b = right_cases.get(case_id)
        if a is None or b is None:
            rows.append(
                {
                    "case_id": case_id,
                    "change": "added" if a is None else "removed",
                    "left": a,
                    "right": b,
                }
            )
            continue
        a_map = _candidate_map(a)
        b_map = _candidate_map(b)
        candidate_deltas: list[dict[str, Any]] = []
        for key in sorted(set(a_map) | set(b_map)):
            av = a_map.get(key)
            bv = b_map.get(key)
            if av is None or bv is None:
                candidate_deltas.append(
                    {
                        "cutoff_date": key[0],
                        "lineage_id": key[1],
                        "change": "new_lineage" if av is None else "missing_lineage",
                    }
                )
                continue
            factor_deltas: dict[str, float] = {}
            for name in set(av.get("risk_factors", {})) | set(bv.get("risk_factors", {})):
                left_score = (av.get("risk_factors", {}).get(name) or {}).get("score")
                right_score = (bv.get("risk_factors", {}).get(name) or {}).get("score")
                if left_score is not None and right_score is not None and left_score != right_score:
                    factor_deltas[name] = round(float(right_score) - float(left_score), 4)
            risk_delta = round(float(bv.get("risk_score", 0.0)) - float(av.get("risk_score", 0.0)), 4)
            if factor_deltas or risk_delta or av.get("alert") != bv.get("alert"):
                candidate_deltas.append(
                    {
                        "cutoff_date": key[0],
                        "lineage_id": key[1],
                        "risk_score_delta": risk_delta,
                        "risk_factor_score_deltas": factor_deltas,
                        "alert": [av.get("alert"), bv.get("alert")],
                    }
                )
        rows.append(
            {
                "case_id": case_id,
                "name": b.get("name", a.get("name")),
                "status": [a.get("status"), b.get("status")],
                "benchmark_valid": [a.get("benchmark_valid"), b.get("benchmark_valid")],
                "lead_time_days": [a.get("lead_time_days"), b.get("lead_time_days")],
                "first_qualified_alert_date": [
                    a.get("first_qualified_alert_date"),
                    b.get("first_qualified_alert_date"),
                ],
                "max_risk_score_delta": round(
                    float(b.get("max_risk_score", 0.0)) - float(a.get("max_risk_score", 0.0)), 4
                ),
                "candidate_differences_by_cutoff_and_lineage": candidate_deltas,
                "lineage_structure_changes": _lineage_structure_changes(a, b),
            }
        )
    return {
        "left_freeze_id": left.get("freeze_id"),
        "right_freeze_id": right.get("freeze_id"),
        "left_manifest_sha256": left.get("manifest_sha256"),
        "right_manifest_sha256": right.get("manifest_sha256"),
        "case_differences": rows,
    }
