from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import mean, median
from typing import Any, Literal

from pydantic import BaseModel, Field

from recallzero.benchmark import (
    BenchmarkAcceptanceCriteria,
    BenchmarkCandidateConfig,
    BenchmarkCandidateRecord,
    BenchmarkCaseResult,
    BenchmarkRunResult,
    load_candidates,
    wilson_interval,
)
from recallzero.config import Settings
from recallzero.freeze import load_freeze_manifest, sha256_file, verify_freeze
from recallzero.models import ComplaintCluster, EmbeddingMethod, Recall
from recallzero.pipeline import build_pipeline
from recallzero.recall import RecallMatcher

AdjudicationClass = Literal[
    "TARGET_RECALL_ASSOCIATED",
    "FUTURE_RECALL_ASSOCIATED",
    "VISIBLE_RECALL_ASSOCIATED",
    "UNCONFIRMED_ALERT",
]


class AlertAdjudication(BaseModel):
    lineage_id: str
    first_alert_date: date
    last_alert_date: date
    snapshot_count: int
    classification: AdjudicationClass
    campaign_number: str | None = None
    recall_date: date | None = None
    match_score: float | None = None
    match_breakdown: dict[str, float] = Field(default_factory=dict)
    signal_ids: tuple[str, ...] = Field(default_factory=tuple)
    evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    note: str = ""


class AdjudicatedBenchmarkCase(BaseModel):
    case: BenchmarkCaseResult
    alert_adjudications: tuple[AlertAdjudication, ...] = Field(default_factory=tuple)


class AdjudicatedBenchmarkRun(BaseModel):
    benchmark_run_id: str
    benchmark_id: str
    benchmark_split: str
    freeze_id: str
    freeze_verified: bool
    lock_verified: bool
    manifest_sha256: str
    adjudication_manifest_sha256: str
    target_match_threshold: float
    cases: tuple[AdjudicatedBenchmarkCase, ...]
    aggregate: dict[str, Any]
    acceptance_criteria: BenchmarkAcceptanceCriteria
    metric_caveats: tuple[str, ...] = Field(default_factory=tuple)
    adjudicated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def _candidate_instances(case: BenchmarkCaseResult) -> dict[str, list[tuple[date, BenchmarkCandidateRecord]]]:
    values: dict[str, list[tuple[date, BenchmarkCandidateRecord]]] = defaultdict(list)
    for snapshot in case.snapshots:
        for candidate in snapshot.top_candidates:
            if candidate.alert:
                values[candidate.lineage_id].append((snapshot.cutoff_date, candidate))
    for lineage in values:
        values[lineage].sort(key=lambda item: (item[0], -item[1].risk_score))
    return values


def _cluster_and_evidence(case: BenchmarkCaseResult, candidate: BenchmarkCandidateRecord):
    evidence = [
        case.alert_evidence[item_id]
        for item_id in candidate.member_ids
        if item_id in case.alert_evidence
    ]
    complaints = [item.complaint for item in evidence]
    signatures = [item.signature for item in evidence]
    if complaints:
        first_date = min(item.received_date for item in complaints)
        last_date = max(item.received_date for item in complaints)
    else:
        # Context should normally be complete. Preserve adjudication rather than
        # rerunning the detector if an old raw artifact lacks member evidence.
        first_date = case.boundary_date
        last_date = case.boundary_date
    try:
        embedding_method = EmbeddingMethod(candidate.embedding_method)
    except ValueError:
        embedding_method = EmbeddingMethod.TFIDF
    cluster = ComplaintCluster(
        cluster_id=candidate.cluster_id or candidate.signal_id,
        label=candidate.issue,
        system=candidate.system,
        failure_mode=candidate.failure_mode,
        defect_family=candidate.defect_family,
        failure_mechanism=candidate.failure_mechanism,
        consequence_family=candidate.consequence_family,
        member_ids=tuple(item.complaint.odi_number for item in evidence),
        source_systems=candidate.source_systems,
        first_received_date=first_date,
        last_received_date=last_date,
        is_meta=candidate.signal_scope == "meta",
        embedding_method=embedding_method,
    )
    return cluster, signatures, complaints


def _best_score(
    *,
    matcher: RecallMatcher,
    case: BenchmarkCaseResult,
    instances: list[tuple[date, BenchmarkCandidateRecord]],
    recalls: list[Recall],
) -> tuple[float, Recall | None, dict[str, float]]:
    best_score = 0.0
    best_recall: Recall | None = None
    best_breakdown: dict[str, float] = {}
    for _, candidate in instances:
        cluster, signatures, complaints = _cluster_and_evidence(case, candidate)
        if not signatures and candidate.member_ids:
            # Old/incomplete raw artifacts cannot be exactly adjudicated. Do not
            # infer a match from label text alone.
            continue
        for recall in recalls:
            details = matcher.score_target_details(cluster, signatures, recall, complaints)
            score = float(details["final"])
            if score > best_score:
                best_score = score
                best_recall = recall
                best_breakdown = {key: float(value) for key, value in details.items()}
    return best_score, best_recall, best_breakdown


async def _recalls_for_case(settings: Settings, case: BenchmarkCaseResult, *, refresh: bool) -> list[Recall]:
    pipeline = build_pipeline(settings, use_nim=False)
    if not refresh:
        cached = pipeline.repository.load_recalls(case.vehicle)
        if cached is not None:
            return cached
    recalls, raw = await pipeline.nhtsa.fetch_recalls(case.vehicle)
    pipeline.repository.save_raw(case.vehicle, "recalls", raw)
    pipeline.repository.save_recalls(case.vehicle, recalls)
    return recalls


async def adjudicate_benchmark(
    *,
    settings: Settings,
    raw_result_path: Path,
    candidates_path: Path,
    freeze_manifest_path: Path | None = None,
    refresh: bool = False,
) -> AdjudicatedBenchmarkRun:
    """Adjudicate frozen alerts *after* detector output has been persisted.

    No extraction, clustering, risk calculation, or detector snapshot is rerun here.
    Future recall text is exposed only to the frozen alert representation stored in
    the raw benchmark artifact.
    """

    raw = BenchmarkRunResult.model_validate_json(raw_result_path.read_text(encoding="utf-8"))
    if raw.manifest_sha256 != sha256_file(candidates_path):
        raise RuntimeError("Candidate manifest changed after the raw benchmark run; adjudication is not comparable")

    freeze_path = freeze_manifest_path or Path(raw.freeze_manifest_path)
    verification = verify_freeze(freeze_path, settings)
    if not verification.ok or verification.freeze_id != raw.freeze_id:
        raise RuntimeError("Detector freeze no longer matches the raw benchmark; adjudication aborted")

    freeze = load_freeze_manifest(freeze_path)
    threshold = float((freeze.get("evaluation", {}) or {}).get("target_match_threshold", 0.45))
    matcher = RecallMatcher()
    manifest_cases = {item.case_id: item for item in load_candidates(candidates_path)}
    adjudicated_cases: list[AdjudicatedBenchmarkCase] = []

    for case in raw.cases:
        config = manifest_cases.get(case.case_id)
        if config is None:
            raise RuntimeError(f"Raw case {case.case_id} is absent from the locked candidate manifest")
        instances_by_lineage = _candidate_instances(case)
        if not instances_by_lineage or not case.benchmark_valid:
            adjudicated_cases.append(AdjudicatedBenchmarkCase(case=case))
            continue

        recalls: list[Recall] = []
        if case.expected_role == "negative":
            recalls = await _recalls_for_case(settings, case, refresh=refresh)
        rows: list[AlertAdjudication] = []

        for lineage_id, instances in sorted(instances_by_lineage.items()):
            dates = [item[0] for item in instances]
            first_alert = min(dates)
            last_alert = max(dates)
            signal_ids = tuple(sorted({item[1].signal_id for item in instances}))
            evidence_ids = tuple(sorted({mid for _, item in instances for mid in item.member_ids}))

            # Positive target attribution is already computed after each frozen
            # detector snapshot. Use that persisted score first, without fetching any
            # additional future data.
            if case.expected_role == "positive":
                target_rows = [
                    (candidate.posthoc_target_score or 0.0, candidate.posthoc_target_breakdown)
                    for _, candidate in instances
                ]
                best_target_score, best_target_breakdown = max(
                    target_rows, key=lambda item: item[0], default=(0.0, {})
                )
                if best_target_score >= threshold:
                    rows.append(
                        AlertAdjudication(
                            lineage_id=lineage_id,
                            first_alert_date=first_alert,
                            last_alert_date=last_alert,
                            snapshot_count=len(instances),
                            classification="TARGET_RECALL_ASSOCIATED",
                            campaign_number=case.campaign_number,
                            recall_date=case.boundary_date,
                            match_score=best_target_score,
                            match_breakdown=best_target_breakdown,
                            signal_ids=signal_ids,
                            evidence_ids=evidence_ids,
                            note="Target match was computed post-hoc after the detector snapshot was frozen.",
                        )
                    )
                    continue

            # If the frozen detector itself already matched a recall that was public
            # at alert time, preserve that classification directly from raw output.
            visible = [
                candidate
                for _, candidate in instances
                if candidate.visible_recall_matched and candidate.visible_recall_campaign_number
            ]
            if visible:
                best_visible = max(visible, key=lambda item: item.visible_recall_score or 0.0)
                rows.append(
                    AlertAdjudication(
                        lineage_id=lineage_id,
                        first_alert_date=first_alert,
                        last_alert_date=last_alert,
                        snapshot_count=len(instances),
                        classification="VISIBLE_RECALL_ASSOCIATED",
                        campaign_number=best_visible.visible_recall_campaign_number,
                        match_score=best_visible.visible_recall_score,
                        match_breakdown=best_visible.visible_recall_breakdown,
                        signal_ids=signal_ids,
                        evidence_ids=evidence_ids,
                        note="Association was already visible to the frozen detector at alert time.",
                    )
                )
                continue

            if case.expected_role == "negative":
                assert config.adjudication_end_date is not None
                future = [
                    recall
                    for recall in recalls
                    if recall.report_received_date is not None
                    and first_alert < recall.report_received_date <= config.adjudication_end_date
                ]
                score, matched_recall, breakdown = _best_score(
                    matcher=matcher,
                    case=case,
                    instances=instances,
                    recalls=future,
                )
                if matched_recall is not None and score >= threshold:
                    rows.append(
                        AlertAdjudication(
                            lineage_id=lineage_id,
                            first_alert_date=first_alert,
                            last_alert_date=last_alert,
                            snapshot_count=len(instances),
                            classification="FUTURE_RECALL_ASSOCIATED",
                            campaign_number=matched_recall.campaign_number,
                            recall_date=matched_recall.report_received_date,
                            match_score=score,
                            match_breakdown=breakdown,
                            signal_ids=signal_ids,
                            evidence_ids=evidence_ids,
                            note="Future recall was exposed only during post-hoc adjudication.",
                        )
                    )
                    continue

            rows.append(
                AlertAdjudication(
                    lineage_id=lineage_id,
                    first_alert_date=first_alert,
                    last_alert_date=last_alert,
                    snapshot_count=len(instances),
                    classification="UNCONFIRMED_ALERT",
                    signal_ids=signal_ids,
                    evidence_ids=evidence_ids,
                    note=(
                        "No qualifying recall association was established within the preregistered adjudication horizon. "
                        "Unconfirmed is an operational false-alert burden metric, not proof that the underlying complaint pattern was invalid."
                    ),
                )
            )

        adjudicated_cases.append(
            AdjudicatedBenchmarkCase(case=case, alert_adjudications=tuple(rows))
        )

    aggregate = aggregate_adjudicated(
        adjudicated_cases,
        acceptance=raw.acceptance_criteria,
        freeze_verified=raw.freeze_verified and verification.ok,
        lock_verified=raw.lock_verified,
    )
    return AdjudicatedBenchmarkRun(
        benchmark_run_id=raw.benchmark_run_id,
        benchmark_id=raw.benchmark_id,
        benchmark_split=raw.benchmark_split,
        freeze_id=raw.freeze_id,
        freeze_verified=raw.freeze_verified and verification.ok,
        lock_verified=raw.lock_verified,
        manifest_sha256=raw.manifest_sha256,
        adjudication_manifest_sha256=sha256_file(candidates_path),
        target_match_threshold=threshold,
        cases=tuple(adjudicated_cases),
        aggregate=aggregate,
        acceptance_criteria=raw.acceptance_criteria,
        metric_caveats=(
            "UNCONFIRMED_ALERT is an operational burden label, not evidence that a safety concern was false.",
            "TAX-001 can fragment one physical pattern across multiple lineage IDs; inspect evidence overlap before interpreting unique-lineage counts.",
            "Future recall adjudication is post-hoc and never changes frozen detector risk scores, clusters, or alert decisions.",
        ),
    )


def aggregate_adjudicated(
    cases: list[AdjudicatedBenchmarkCase],
    *,
    acceptance: BenchmarkAcceptanceCriteria,
    freeze_verified: bool,
    lock_verified: bool,
) -> dict[str, Any]:
    raw_cases = [item.case for item in cases]
    valid = [item for item in raw_cases if item.benchmark_valid]
    positives = [item for item in valid if item.expected_role == "positive"]
    controls = [item for item in valid if item.expected_role == "negative"]
    qualified = [item for item in positives if item.first_qualified_alert_date is not None]
    lead_times = [item.lead_time_days for item in qualified if item.lead_time_days is not None]

    adjudications = [
        row
        for item in cases
        if item.case.benchmark_valid
        for row in item.alert_adjudications
    ]
    control_adjudications = [
        row
        for item in cases
        if item.case.benchmark_valid and item.case.expected_role == "negative"
        for row in item.alert_adjudications
    ]
    confirmed = [
        row
        for row in adjudications
        if row.classification
        in {"TARGET_RECALL_ASSOCIATED", "FUTURE_RECALL_ASSOCIATED", "VISIBLE_RECALL_ASSOCIATED"}
    ]
    unconfirmed = [row for row in adjudications if row.classification == "UNCONFIRMED_ALERT"]
    future_control = [
        row for row in control_adjudications if row.classification == "FUTURE_RECALL_ASSOCIATED"
    ]
    unconfirmed_control = [
        row for row in control_adjudications if row.classification == "UNCONFIRMED_ALERT"
    ]

    control_case_unconfirmed: set[str] = set()
    unconfirmed_snapshots = 0
    replay_years = sum(item.replay_years for item in controls)
    for item in cases:
        if not item.case.benchmark_valid or item.case.expected_role != "negative":
            continue
        lineages = {
            row.lineage_id
            for row in item.alert_adjudications
            if row.classification == "UNCONFIRMED_ALERT"
        }
        if lineages:
            control_case_unconfirmed.add(item.case.case_id)
        for snapshot in item.case.snapshots:
            if lineages & set(snapshot.alert_lineage_ids):
                unconfirmed_snapshots += 1

    sensitivity = len(qualified) / len(positives) if positives else None
    control_unconfirmed_rate = (
        len(control_case_unconfirmed) / len(controls) if controls else None
    )
    unconfirmed_per_year = len(unconfirmed_control) / replay_years if replay_years else None
    unconfirmed_snapshots_per_year = unconfirmed_snapshots / replay_years if replay_years else None
    precision = len(confirmed) / len(adjudications) if adjudications else None

    first_margins = [
        item.first_alert_threshold_margin
        for item in valid
        if item.first_alert_threshold_margin is not None
    ]
    first_evidence = [
        item.first_alert_evidence_count for item in valid if item.first_alert_evidence_count is not None
    ]
    persistence = [
        item.max_consecutive_alert_snapshots for item in valid if item.first_alert_date is not None
    ]

    all_anti_leakage = all(
        all(item.anti_leakage_checks.values())
        for item in valid
        if item.anti_leakage_checks
    )
    criteria = {
        "freeze_verified": freeze_verified,
        "manifest_lock_verified": lock_verified,
        "positive_sensitivity": (
            sensitivity is not None and sensitivity >= acceptance.positive_sensitivity_min
        ),
        "median_qualified_lead_time_days": (
            bool(lead_times)
            and median(lead_times) >= acceptance.median_qualified_lead_time_days_min
        ),
        "unconfirmed_alert_lineages_per_vehicle_replay_year": (
            unconfirmed_per_year is not None
            and unconfirmed_per_year
            <= acceptance.unconfirmed_alert_lineages_per_vehicle_replay_year_max
        ),
        "control_cases_with_unconfirmed_alerts_rate": (
            control_unconfirmed_rate is not None
            and control_unconfirmed_rate
            <= acceptance.control_cases_with_unconfirmed_alerts_rate_max
        ),
        "anti_leakage": all_anti_leakage if acceptance.anti_leakage_required else True,
        "no_invalid_cases": len(valid) == len(raw_cases),
    }

    return {
        "case_count": len(raw_cases),
        "valid_case_count": len(valid),
        "invalid_case_count": len(raw_cases) - len(valid),
        "valid_positive_count": len(positives),
        "valid_control_count": len(controls),
        "qualified_positive_count": len(qualified),
        "positive_sensitivity": round(sensitivity, 4) if sensitivity is not None else None,
        "positive_sensitivity_95ci": wilson_interval(len(qualified), len(positives)),
        "lead_time_days": lead_times,
        "lead_time_min": min(lead_times) if lead_times else None,
        "lead_time_median": median(lead_times) if lead_times else None,
        "lead_time_mean": round(mean(lead_times), 2) if lead_times else None,
        "lead_time_max": max(lead_times) if lead_times else None,
        "target_like_but_not_alerted_count": sum(
            item.earliest_target_like_candidate_date is not None
            and item.first_qualified_alert_date is None
            for item in positives
        ),
        "control_cases_with_alerts": sum(item.first_alert_date is not None for item in controls),
        "control_cases_with_unconfirmed_alerts": len(control_case_unconfirmed),
        "control_cases_with_unconfirmed_alerts_rate": (
            round(control_unconfirmed_rate, 4) if control_unconfirmed_rate is not None else None
        ),
        "control_cases_with_unconfirmed_alerts_rate_95ci": wilson_interval(
            len(control_case_unconfirmed), len(controls)
        ),
        "future_recall_associated_control_alerts": len(future_control),
        "unconfirmed_control_alerts": len(unconfirmed_control),
        "negative_replay_years": round(replay_years, 4),
        "unconfirmed_alert_snapshots": unconfirmed_snapshots,
        "unconfirmed_alert_snapshots_per_vehicle_replay_year": (
            round(unconfirmed_snapshots_per_year, 4)
            if unconfirmed_snapshots_per_year is not None
            else None
        ),
        "unconfirmed_alert_lineages_per_vehicle_replay_year": (
            round(unconfirmed_per_year, 4) if unconfirmed_per_year is not None else None
        ),
        "confirmed_alert_lineages": len(confirmed),
        "unconfirmed_alert_lineages": len(unconfirmed),
        "recall_associated_alert_precision": round(precision, 4) if precision is not None else None,
        "recall_associated_alert_precision_95ci": wilson_interval(len(confirmed), len(adjudications)),
        "median_threshold_margin_at_first_alert": median(first_margins) if first_margins else None,
        "median_evidence_at_first_alert": median(first_evidence) if first_evidence else None,
        "median_alert_persistence_snapshots": median(persistence) if persistence else None,
        "all_anti_leakage_checks_pass": all_anti_leakage,
        "prototype_acceptance_criteria": criteria,
        "prototype_acceptance_pass": all(criteria.values()),
    }


def build_validation_report(adjudicated: AdjudicatedBenchmarkRun) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for item in adjudicated.cases:
        case = item.case
        counts = defaultdict(int)
        for row in item.alert_adjudications:
            counts[row.classification] += 1
        cases.append(
            {
                "case_id": case.case_id,
                "name": case.name,
                "expected_role": case.expected_role,
                "benchmark_valid": case.benchmark_valid,
                "invalid_reason": case.invalid_reason,
                "status": case.status,
                "first_alert_date": case.first_alert_date,
                "last_alert_date": case.last_alert_date,
                "max_consecutive_alert_snapshots": case.max_consecutive_alert_snapshots,
                "max_alert_duration_days": case.max_alert_duration_days,
                "first_alert_risk_score": case.first_alert_risk_score,
                "first_alert_threshold_margin": case.first_alert_threshold_margin,
                "first_alert_evidence_count": case.first_alert_evidence_count,
                "first_qualified_alert_date": case.first_qualified_alert_date,
                "lead_time_days": case.lead_time_days,
                "max_risk_score": case.max_risk_score,
                "max_threshold_margin": case.max_threshold_margin,
                "alert_snapshot_count": case.alert_snapshot_count,
                "unique_alert_lineages": case.unique_alert_lineages,
                "adjudication_counts": dict(counts),
            }
        )
    return {
        "benchmark_run_id": adjudicated.benchmark_run_id,
        "benchmark_id": adjudicated.benchmark_id,
        "benchmark_split": adjudicated.benchmark_split,
        "freeze_id": adjudicated.freeze_id,
        "freeze_verified": adjudicated.freeze_verified,
        "lock_verified": adjudicated.lock_verified,
        "aggregate": adjudicated.aggregate,
        "acceptance_criteria": adjudicated.acceptance_criteria.model_dump(mode="json"),
        "cases": cases,
        "metric_caveats": list(adjudicated.metric_caveats),
    }


def write_validation_report_csv(adjudicated: AdjudicatedBenchmarkRun, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "case_id",
        "name",
        "expected_role",
        "benchmark_valid",
        "invalid_reason",
        "status",
        "first_alert_date",
        "last_alert_date",
        "max_consecutive_alert_snapshots",
        "max_alert_duration_days",
        "first_alert_risk_score",
        "first_alert_threshold_margin",
        "first_alert_evidence_count",
        "first_qualified_alert_date",
        "lead_time_days",
        "max_risk_score",
        "max_threshold_margin",
        "alert_snapshot_count",
        "unique_alert_lineages",
        "target_recall_associated",
        "future_recall_associated",
        "visible_recall_associated",
        "unconfirmed_alerts",
        "complaint_count_eligible",
        "nim_signature_fraction",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in adjudicated.cases:
            case = item.case
            counts = Counter(row.classification for row in item.alert_adjudications)
            writer.writerow(
                {
                    "case_id": case.case_id,
                    "name": case.name,
                    "expected_role": case.expected_role,
                    "benchmark_valid": case.benchmark_valid,
                    "invalid_reason": case.invalid_reason or "",
                    "status": case.status,
                    "first_alert_date": case.first_alert_date or "",
                    "last_alert_date": case.last_alert_date or "",
                    "max_consecutive_alert_snapshots": case.max_consecutive_alert_snapshots,
                    "max_alert_duration_days": case.max_alert_duration_days,
                    "first_alert_risk_score": case.first_alert_risk_score if case.first_alert_risk_score is not None else "",
                    "first_alert_threshold_margin": case.first_alert_threshold_margin if case.first_alert_threshold_margin is not None else "",
                    "first_alert_evidence_count": case.first_alert_evidence_count if case.first_alert_evidence_count is not None else "",
                    "first_qualified_alert_date": case.first_qualified_alert_date or "",
                    "lead_time_days": case.lead_time_days if case.lead_time_days is not None else "",
                    "max_risk_score": case.max_risk_score,
                    "max_threshold_margin": case.max_threshold_margin if case.max_threshold_margin is not None else "",
                    "alert_snapshot_count": case.alert_snapshot_count,
                    "unique_alert_lineages": case.unique_alert_lineages,
                    "target_recall_associated": counts["TARGET_RECALL_ASSOCIATED"],
                    "future_recall_associated": counts["FUTURE_RECALL_ASSOCIATED"],
                    "visible_recall_associated": counts["VISIBLE_RECALL_ASSOCIATED"],
                    "unconfirmed_alerts": counts["UNCONFIRMED_ALERT"],
                    "complaint_count_eligible": case.input_provenance.complaint_count_eligible,
                    "nim_signature_fraction": case.semantic_provenance.nim_fraction if case.semantic_provenance.nim_fraction is not None else "",
                }
            )
