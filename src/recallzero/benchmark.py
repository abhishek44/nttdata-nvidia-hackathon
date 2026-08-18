from __future__ import annotations

import csv
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from recallzero.backtest import RecallTimeMachine
from recallzero.config import Settings
from recallzero.freeze import FreezeVerification, load_freeze_manifest, verify_freeze
from recallzero.models import Complaint, FailureSignature, Recall, Vehicle
from recallzero.pipeline import RecallZeroPipeline, build_pipeline
from recallzero.utils import stable_id


class BenchmarkCandidateConfig(BaseModel):
    name: str
    make: str
    model: str
    model_years: tuple[int, ...]
    campaign_number: str | None = None
    official_recall_date: date | None = None
    evaluation_end_date: date | None = None
    status: str = "candidate"
    note: str | None = None
    expected_role: Literal["positive", "negative"] = "positive"
    benchmark_split: Literal["development", "holdout"] = "development"
    defect_family: str | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def validate_boundary(self) -> "BenchmarkCandidateConfig":
        if self.expected_role == "positive":
            if not self.campaign_number or not self.official_recall_date:
                raise ValueError("Positive benchmark cases require campaign_number and official_recall_date")
        elif self.evaluation_end_date is None:
            raise ValueError("Negative benchmark cases require evaluation_end_date")
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


class BenchmarkCandidateRecord(BaseModel):
    signal_id: str
    lineage_id: str
    signal_scope: str
    issue: str
    failure_mechanism: str
    consequence_family: str
    evidence_count: int
    risk_score: float
    risk_factors: dict[str, dict[str, Any]] = Field(default_factory=dict)
    alert: bool
    distance_to_alert_threshold: float
    posthoc_target_score: float | None = None
    posthoc_target_breakdown: dict[str, float] = Field(default_factory=dict)


class BenchmarkSnapshotRecord(BaseModel):
    cutoff_date: date
    complaint_count_visible: int
    signal_count: int
    max_risk_score: float
    max_risk_signal_id: str | None = None
    distance_to_alert_threshold: float | None = None
    alert_signal_ids: tuple[str, ...] = Field(default_factory=tuple)
    alert_lineage_ids: tuple[str, ...] = Field(default_factory=tuple)
    top_candidates: tuple[BenchmarkCandidateRecord, ...] = Field(default_factory=tuple)


class BenchmarkCaseResult(BaseModel):
    case_id: str
    name: str
    expected_role: Literal["positive", "negative"]
    benchmark_split: Literal["development", "holdout"]
    vehicle: Vehicle
    campaign_number: str | None = None
    boundary_date: date
    status: str
    earliest_target_like_candidate_date: date | None = None
    first_any_alert_date: date | None = None
    first_qualified_alert_date: date | None = None
    lead_time_days: int | None = None
    alert_snapshot_count: int = 0
    alert_signal_occurrences: int = 0
    max_risk_score: float = 0.0
    max_risk_date: date | None = None
    unique_alert_lineages: int = 0
    replay_days: int = 0
    replay_years: float = 0.0
    anti_leakage_checks: dict[str, bool] = Field(default_factory=dict)
    warnings: tuple[str, ...] = Field(default_factory=tuple)
    snapshots: tuple[BenchmarkSnapshotRecord, ...] = Field(default_factory=tuple)


class BenchmarkRunResult(BaseModel):
    benchmark_run_id: str
    freeze_id: str
    freeze_verified: bool
    manifest_path: str
    freeze_manifest_path: str
    case_count: int
    cases: tuple[BenchmarkCaseResult, ...]
    aggregate: dict[str, Any]
    metric_caveats: tuple[str, ...] = Field(default_factory=tuple)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def load_candidates(path: Path) -> list[BenchmarkCandidateConfig]:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    rows = raw.get("candidates", raw) if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise ValueError("Benchmark manifest must contain a candidates list")
    return [BenchmarkCandidateConfig.model_validate(item) for item in rows if item.get("enabled", True)]


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


def _snapshot_from_backtest(snapshot) -> BenchmarkSnapshotRecord:
    return BenchmarkSnapshotRecord(
        cutoff_date=snapshot.cutoff_date,
        complaint_count_visible=snapshot.complaint_count_visible,
        signal_count=snapshot.signal_count,
        max_risk_score=snapshot.max_risk_score,
        max_risk_signal_id=snapshot.max_risk_signal_id,
        distance_to_alert_threshold=snapshot.distance_to_alert_threshold,
        alert_signal_ids=tuple(signal.signal_id for signal in snapshot.alerts),
        alert_lineage_ids=tuple(signal.lineage_id for signal in snapshot.alerts),
        top_candidates=tuple(
            BenchmarkCandidateRecord(
                signal_id=item.signal_id,
                lineage_id=item.lineage_id,
                signal_scope=item.signal_scope,
                issue=item.issue,
                failure_mechanism=item.failure_mechanism,
                consequence_family=item.consequence_family,
                evidence_count=item.evidence_count,
                risk_score=item.risk_score,
                risk_factors={
                    name: value.model_dump(mode="python")
                    for name, value in item.risk_factors.items()
                },
                alert=item.alert,
                distance_to_alert_threshold=item.distance_to_alert_threshold,
                posthoc_target_score=item.posthoc_target_score,
                posthoc_target_breakdown=item.posthoc_target_breakdown,
            )
            for item in snapshot.top_candidates
        ),
    )


def _schedule(replay_start: date, boundary: date) -> list[date]:
    return RecallTimeMachine._cutoff_schedule(replay_start, boundary)


async def _run_negative_control(
    *,
    pipeline: RecallZeroPipeline,
    case: BenchmarkCandidateConfig,
    complaints: Sequence[Complaint],
    recalls: Sequence[Recall],
    use_signature_cache: bool,
    top_candidate_count: int,
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
    alert_lineages: set[str] = set()
    alert_occurrences = 0
    degraded_count = 0

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
        alert_lineages.update(signal.lineage_id for signal in alerts)
        ranked = sorted(run.signals, key=lambda item: (-item.risk.final_score, item.cluster.label))[
            : max(1, top_candidate_count)
        ]
        max_signal = ranked[0] if ranked else None
        snapshots.append(
            BenchmarkSnapshotRecord(
                cutoff_date=cutoff,
                complaint_count_visible=len(visible),
                signal_count=len(run.signals),
                max_risk_score=max_signal.risk.final_score if max_signal else 0.0,
                max_risk_signal_id=max_signal.signal_id if max_signal else None,
                distance_to_alert_threshold=(
                    round(max(0.0, pipeline.risk_config.alert_threshold - max_signal.risk.final_score), 2)
                    if max_signal
                    else pipeline.risk_config.alert_threshold
                ),
                alert_signal_ids=tuple(signal.signal_id for signal in alerts),
                alert_lineage_ids=tuple(signal.lineage_id for signal in alerts),
                top_candidates=tuple(
                    BenchmarkCandidateRecord(
                        signal_id=signal.signal_id,
                        lineage_id=signal.lineage_id,
                        signal_scope=signal.signal_scope,
                        issue=signal.cluster.label,
                        failure_mechanism=signal.cluster.failure_mechanism,
                        consequence_family=signal.cluster.consequence_family,
                        evidence_count=signal.cluster.evidence_count,
                        risk_score=signal.risk.final_score,
                        risk_factors=_risk_factor_map(signal),
                        alert=signal.risk.alert and not degraded,
                        distance_to_alert_threshold=round(
                            max(0.0, pipeline.risk_config.alert_threshold - signal.risk.final_score), 2
                        ),
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
    status = "CONTROL_ALERT_PRESENT" if first_any else "CONTROL_QUIET"
    if not all(checks.values()):
        status = "INVALID_CONTROL_REPLAY"

    return BenchmarkCaseResult(
        case_id=case.case_id,
        name=case.name,
        expected_role=case.expected_role,
        benchmark_split=case.benchmark_split,
        vehicle=case.vehicle,
        campaign_number=None,
        boundary_date=boundary,
        status=status,
        first_any_alert_date=first_any,
        alert_snapshot_count=len(alert_snapshots),
        alert_signal_occurrences=alert_occurrences,
        max_risk_score=max_snapshot.max_risk_score if max_snapshot else 0.0,
        max_risk_date=max_snapshot.cutoff_date if max_snapshot else None,
        unique_alert_lineages=len(alert_lineages),
        replay_days=replay_days,
        replay_years=round(replay_days / 365.25, 4),
        anti_leakage_checks=checks,
        warnings=tuple(warnings),
        snapshots=tuple(snapshots),
    )


async def run_benchmark(
    *,
    settings: Settings,
    candidates_path: Path,
    freeze_manifest_path: Path,
    refresh: bool = False,
    allow_freeze_mismatch: bool = False,
    pipeline_factory=build_pipeline,
) -> tuple[BenchmarkRunResult, FreezeVerification]:
    verification = verify_freeze(freeze_manifest_path, settings)
    if not verification.ok and not allow_freeze_mismatch:
        details = "; ".join(
            f"{item.name}: expected {item.expected}, actual {item.actual}"
            for item in verification.failures[:6]
        )
        raise RuntimeError(f"Detector freeze mismatch; benchmark comparability is invalid. {details}")

    manifest = load_freeze_manifest(freeze_manifest_path)
    evaluation = manifest.get("evaluation", {}) or {}
    target_match_threshold = float(evaluation.get("target_match_threshold", 0.45))
    top_candidate_count = int(evaluation.get("top_candidate_count", 5))

    cases: list[BenchmarkCaseResult] = []
    for case in load_candidates(candidates_path):
        pipeline = pipeline_factory(settings, use_nim=settings.use_nim)
        complaints, recalls = await pipeline.ingest(case.vehicle, refresh=refresh)

        if case.expected_role == "positive":
            assert case.campaign_number is not None
            assert case.official_recall_date is not None
            clean_campaign = case.campaign_number.upper().replace("-", "")
            target = next((item for item in recalls if item.campaign_number == clean_campaign), None)
            if target is None:
                target = await pipeline.nhtsa.fetch_campaign(clean_campaign)
            if target is None:
                raise RuntimeError(f"Campaign {clean_campaign} could not be retrieved for case {case.name}")

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
            snapshots = tuple(_snapshot_from_backtest(item) for item in result.snapshots)
            max_snapshot = max(snapshots, key=lambda item: item.max_risk_score, default=None)
            alert_lineages = {
                lineage
                for snapshot in snapshots
                for lineage in snapshot.alert_lineage_ids
            }
            alert_occurrences = sum(len(snapshot.alert_signal_ids) for snapshot in snapshots)
            replay_days = (
                (case.boundary_date - snapshots[0].cutoff_date).days if snapshots else 0
            )
            cases.append(
                BenchmarkCaseResult(
                    case_id=case.case_id,
                    name=case.name,
                    expected_role=case.expected_role,
                    benchmark_split=case.benchmark_split,
                    vehicle=case.vehicle,
                    campaign_number=clean_campaign,
                    boundary_date=case.boundary_date,
                    status=result.status,
                    earliest_target_like_candidate_date=result.earliest_target_like_candidate_date,
                    first_any_alert_date=result.first_any_alert_date,
                    first_qualified_alert_date=result.first_qualified_alert_date,
                    lead_time_days=result.lead_time_days,
                    alert_snapshot_count=result.alert_snapshot_count,
                    alert_signal_occurrences=alert_occurrences,
                    max_risk_score=max_snapshot.max_risk_score if max_snapshot else 0.0,
                    max_risk_date=max_snapshot.cutoff_date if max_snapshot else None,
                    unique_alert_lineages=len(alert_lineages),
                    replay_days=replay_days,
                    replay_years=round(replay_days / 365.25, 4),
                    anti_leakage_checks=result.anti_leakage_checks,
                    warnings=result.warnings,
                    snapshots=snapshots,
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
                )
            )

    aggregate = aggregate_benchmark(cases)
    run_id = stable_id(
        "bench",
        manifest.get("freeze_id", "unknown"),
        *(case.case_id for case in cases),
    )
    result = BenchmarkRunResult(
        benchmark_run_id=run_id,
        freeze_id=str(manifest.get("freeze_id", "unknown")),
        freeze_verified=verification.ok,
        manifest_path=str(candidates_path),
        freeze_manifest_path=str(freeze_manifest_path),
        case_count=len(cases),
        cases=tuple(cases),
        aggregate=aggregate,
        metric_caveats=(
            "unique false signal lineages can be inflated by taxonomy-driven lineage fragmentation; inspect lineage composition before interpreting this metric as distinct detector failures.",
        ),
    )
    return result, verification


def aggregate_benchmark(cases: Sequence[BenchmarkCaseResult]) -> dict[str, Any]:
    positives = [item for item in cases if item.expected_role == "positive"]
    negatives = [item for item in cases if item.expected_role == "negative"]
    qualified = [item for item in positives if item.first_qualified_alert_date is not None]
    lead_times = [item.lead_time_days for item in qualified if item.lead_time_days is not None]
    negative_alert_cases = [item for item in negatives if item.first_any_alert_date is not None]
    replay_years = sum(item.replay_years for item in negatives)
    false_alert_snapshots = sum(item.alert_snapshot_count for item in negatives)
    false_lineages = sum(item.unique_alert_lineages for item in negatives)

    return {
        "positive_case_count": len(positives),
        "positive_qualified_count": len(qualified),
        "positive_sensitivity": round(len(qualified) / len(positives), 4) if positives else None,
        "lead_time_days": lead_times,
        "median_lead_time_days": median(lead_times) if lead_times else None,
        "negative_case_count": len(negatives),
        "negative_cases_with_alerts": len(negative_alert_cases),
        "negative_case_alert_rate": (
            round(len(negative_alert_cases) / len(negatives), 4) if negatives else None
        ),
        "negative_replay_years": round(replay_years, 4),
        "false_alert_snapshots": false_alert_snapshots,
        "unique_false_lineages": false_lineages,
        "false_alert_snapshots_per_vehicle_replay_year": (
            round(false_alert_snapshots / replay_years, 4) if replay_years else None
        ),
        "unique_false_lineages_per_vehicle_replay_year": (
            round(false_lineages / replay_years, 4) if replay_years else None
        ),
    }


def write_benchmark_csv(result: BenchmarkRunResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "case_id",
        "name",
        "expected_role",
        "benchmark_split",
        "make",
        "model",
        "model_years",
        "campaign_number",
        "boundary_date",
        "status",
        "earliest_target_like_candidate_date",
        "first_any_alert_date",
        "first_qualified_alert_date",
        "lead_time_days",
        "alert_snapshot_count",
        "alert_signal_occurrences",
        "max_risk_score",
        "max_risk_date",
        "unique_alert_lineages",
        "replay_years",
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
                    "make": item.vehicle.make,
                    "model": item.vehicle.model,
                    "model_years": ",".join(str(year) for year in item.vehicle.model_years),
                    "campaign_number": item.campaign_number or "",
                    "boundary_date": item.boundary_date.isoformat(),
                    "status": item.status,
                    "earliest_target_like_candidate_date": item.earliest_target_like_candidate_date or "",
                    "first_any_alert_date": item.first_any_alert_date or "",
                    "first_qualified_alert_date": item.first_qualified_alert_date or "",
                    "lead_time_days": item.lead_time_days if item.lead_time_days is not None else "",
                    "alert_snapshot_count": item.alert_snapshot_count,
                    "alert_signal_occurrences": item.alert_signal_occurrences,
                    "max_risk_score": item.max_risk_score,
                    "max_risk_date": item.max_risk_date or "",
                    "unique_alert_lineages": item.unique_alert_lineages,
                    "replay_years": item.replay_years,
                }
            )


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
        factor_deltas: dict[str, float] = {}
        a_snapshots = {item["cutoff_date"]: item for item in a.get("snapshots", [])}
        b_snapshots = {item["cutoff_date"]: item for item in b.get("snapshots", [])}
        common_dates = sorted(set(a_snapshots) & set(b_snapshots))
        for cutoff in common_dates:
            a_top = (a_snapshots[cutoff].get("top_candidates") or [{}])[0]
            b_top = (b_snapshots[cutoff].get("top_candidates") or [{}])[0]
            for name in set(a_top.get("risk_factors", {})) | set(b_top.get("risk_factors", {})):
                av = (a_top.get("risk_factors", {}).get(name) or {}).get("score")
                bv = (b_top.get("risk_factors", {}).get(name) or {}).get("score")
                if av is not None and bv is not None and av != bv:
                    factor_deltas[f"{cutoff}:{name}"] = round(float(bv) - float(av), 4)
        rows.append(
            {
                "case_id": case_id,
                "name": b.get("name", a.get("name")),
                "status": [a.get("status"), b.get("status")],
                "lead_time_days": [a.get("lead_time_days"), b.get("lead_time_days")],
                "first_qualified_alert_date": [
                    a.get("first_qualified_alert_date"),
                    b.get("first_qualified_alert_date"),
                ],
                "max_risk_score_delta": round(
                    float(b.get("max_risk_score", 0.0)) - float(a.get("max_risk_score", 0.0)), 4
                ),
                "risk_factor_score_deltas": factor_deltas,
            }
        )
    return {
        "left_freeze_id": left.get("freeze_id"),
        "right_freeze_id": right.get("freeze_id"),
        "case_differences": rows,
    }
