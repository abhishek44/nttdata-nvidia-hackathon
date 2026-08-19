from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from recallzero.benchmark import BenchmarkCandidateRecord, BenchmarkRunResult


class RiskFactorAudit(BaseModel):
    name: str
    score: float
    weight: float
    contribution: float
    max_contribution: float
    weighted_headroom: float
    risk_if_factor_perfect: float
    alone_could_cross_threshold: bool
    explanation: str = ""


class TargetLineageAudit(BaseModel):
    lineage_id: str
    signal_scope: str
    representative_issue: str
    occurrence_count: int
    first_date: date
    last_date: date
    best_target_score: float
    best_target_date: date
    best_target_breakdown: dict[str, float] = Field(default_factory=dict)
    max_risk_score: float
    max_risk_date: date
    max_threshold_margin: float
    max_evidence_count: int
    ever_alerted: bool
    risk_threshold_crossed: bool
    alert_withheld_despite_threshold: bool
    first_alert_date: date | None = None
    risk_factors_at_max_risk: tuple[RiskFactorAudit, ...] = Field(default_factory=tuple)
    dominant_risk_headroom: tuple[str, ...] = Field(default_factory=tuple)


class TargetRiskAuditCase(BaseModel):
    case_id: str
    name: str
    campaign_number: str | None = None
    detector_status: str
    benchmark_valid: bool
    invalid_reason: str | None = None
    persisted_candidate_occurrences: int = 0
    persisted_unique_lineages: int = 0
    any_alert: bool = False
    any_target_matched_alert: bool = False
    best_persisted_target_score: float | None = None
    best_persisted_target_lineage_id: str | None = None
    best_persisted_target_lineage: TargetLineageAudit | None = None
    best_alerted_lineage: TargetLineageAudit | None = None
    top_target_lineages: tuple[TargetLineageAudit, ...] = Field(default_factory=tuple)
    diagnosis: str
    caveats: tuple[str, ...] = Field(default_factory=tuple)


class TargetRiskAuditResult(BaseModel):
    audit_id: str = "target-risk-audit-v1"
    scope: str = "development_diagnostic_only"
    source_benchmark_run_id: str
    source_freeze_id: str
    source_freeze_verified: bool
    source_lock_verified: bool
    target_match_threshold: float
    alert_threshold: float
    detector_recomputed: bool = False
    llm_calls_required: int = 0
    embedding_calls_required: int = 0
    persisted_candidate_scope: str = "top_n_by_risk_plus_all_alerts"
    positive_case_count: int
    valid_positive_case_count: int
    invalid_positive_case_count: int
    cases: tuple[TargetRiskAuditCase, ...]
    caveats: tuple[str, ...] = Field(default_factory=tuple)


def _infer_alert_threshold(raw: BenchmarkRunResult) -> float:
    inferred: list[float] = []
    for case in raw.cases:
        for snapshot in case.snapshots:
            for candidate in snapshot.top_candidates:
                inferred.append(round(candidate.risk_score - candidate.threshold_margin, 2))
    if not inferred:
        return 75.0
    # Candidate records are generated under one frozen threshold. Median protects
    # against any legacy rounding noise without importing detector configuration.
    values = sorted(inferred)
    return values[len(values) // 2]


def _factor_audits(
    candidate: BenchmarkCandidateRecord,
    *,
    alert_threshold: float,
) -> tuple[RiskFactorAudit, ...]:
    rows: list[RiskFactorAudit] = []
    for name, raw_factor in candidate.risk_factors.items():
        try:
            score = float(raw_factor.get("score", 0.0))
            weight = float(raw_factor.get("weight", 0.0))
            contribution = float(raw_factor.get("contribution", score * weight))
        except (TypeError, ValueError):
            continue
        max_contribution = 100.0 * weight
        headroom = max(0.0, max_contribution - contribution)
        risk_if_perfect = min(100.0, candidate.risk_score + headroom)
        rows.append(
            RiskFactorAudit(
                name=name,
                score=round(score, 2),
                weight=round(weight, 4),
                contribution=round(contribution, 2),
                max_contribution=round(max_contribution, 2),
                weighted_headroom=round(headroom, 2),
                risk_if_factor_perfect=round(risk_if_perfect, 2),
                alone_could_cross_threshold=risk_if_perfect >= alert_threshold,
                explanation=str(raw_factor.get("explanation") or ""),
            )
        )
    rows.sort(key=lambda row: (-row.weighted_headroom, -row.weight, row.name))
    return tuple(rows)


def _summarize_lineage(
    occurrences: list[tuple[date, BenchmarkCandidateRecord]],
    *,
    alert_threshold: float,
) -> TargetLineageAudit:
    # Select the most target-compatible occurrence, then the maximum-risk
    # occurrence independently. This avoids conflating attribution with risk.
    best_target_date, best_target = max(
        occurrences,
        key=lambda row: (
            row[1].posthoc_target_score if row[1].posthoc_target_score is not None else -1.0,
            row[1].risk_score,
            -row[0].toordinal(),
        ),
    )
    max_risk_date, max_risk = max(
        occurrences,
        key=lambda row: (row[1].risk_score, row[1].evidence_count, -row[0].toordinal()),
    )
    alert_dates = sorted(row[0] for row in occurrences if row[1].alert)
    factor_rows = _factor_audits(max_risk, alert_threshold=alert_threshold)
    return TargetLineageAudit(
        lineage_id=max_risk.lineage_id,
        signal_scope=max_risk.signal_scope,
        representative_issue=best_target.issue,
        occurrence_count=len(occurrences),
        first_date=min(row[0] for row in occurrences),
        last_date=max(row[0] for row in occurrences),
        best_target_score=round(float(best_target.posthoc_target_score or 0.0), 4),
        best_target_date=best_target_date,
        best_target_breakdown={
            key: round(float(value), 4)
            for key, value in (best_target.posthoc_target_breakdown or {}).items()
        },
        max_risk_score=round(max_risk.risk_score, 2),
        max_risk_date=max_risk_date,
        max_threshold_margin=round(max_risk.threshold_margin, 2),
        max_evidence_count=max(row[1].evidence_count for row in occurrences),
        ever_alerted=bool(alert_dates),
        risk_threshold_crossed=max_risk.risk_score >= alert_threshold,
        alert_withheld_despite_threshold=(max_risk.risk_score >= alert_threshold and not bool(alert_dates)),
        first_alert_date=alert_dates[0] if alert_dates else None,
        risk_factors_at_max_risk=factor_rows,
        dominant_risk_headroom=tuple(row.name for row in factor_rows[:3]),
    )


def _diagnosis(
    *,
    case_status: str,
    benchmark_valid: bool,
    best: TargetLineageAudit | None,
    best_alerted: TargetLineageAudit | None,
    target_match_threshold: float,
) -> str:
    if not benchmark_valid:
        return "INVALID_CASE"
    if case_status == "EARLY_SIGNAL_DETECTED":
        return "QUALIFIED_EARLY_SIGNAL"
    if best is None:
        return "NO_PERSISTED_TARGET_CANDIDATE"

    target_match = best.best_target_score >= target_match_threshold
    if target_match and best.ever_alerted:
        return "TARGET_MATCHED_AND_ALERTED_IN_PERSISTED_LINEAGE"
    if target_match and best.risk_threshold_crossed:
        return "TARGET_MATCHED_RISK_THRESHOLD_CROSSED_BUT_ALERT_WITHHELD"
    if target_match:
        return "TARGET_MATCHED_BUT_RISK_GATE_NOT_MET"
    if best.ever_alerted:
        return "TARGET_LINEAGE_ALERTED_BUT_ATTRIBUTION_BELOW_THRESHOLD"
    if best.risk_threshold_crossed:
        return "TARGET_LINEAGE_RISK_THRESHOLD_CROSSED_BUT_ATTRIBUTION_BELOW_THRESHOLD"
    if best_alerted is not None:
        return "BEST_TARGET_LINEAGE_NOT_ALERTED_AND_OFF_TARGET_ALERTS_PRESENT"
    return "BEST_TARGET_LINEAGE_BELOW_ATTRIBUTION_AND_RISK_GATES"


def build_target_risk_audit(
    *,
    raw_result_path: Path,
    target_match_threshold: float = 0.45,
    top_lineages: int = 8,
) -> TargetRiskAuditResult:
    """Audit deterministic risk factors on persisted positive-case candidate lineages.

    This function never reruns the detector or calls NIM. It operates only on the
    raw benchmark artifact, whose snapshots persist the ordinary top-N risk
    candidates plus every alert candidate with their risk-factor decomposition.
    """
    raw = BenchmarkRunResult.model_validate_json(raw_result_path.read_text(encoding="utf-8"))
    if not raw.freeze_verified or not raw.lock_verified:
        raise RuntimeError(
            "Target Risk Audit requires a freeze-verified and lock-verified source benchmark"
        )
    if not (0.0 <= target_match_threshold <= 1.0):
        raise ValueError("target_match_threshold must be between 0 and 1")
    if top_lineages < 1:
        raise ValueError("top_lineages must be at least 1")

    alert_threshold = _infer_alert_threshold(raw)
    case_rows: list[TargetRiskAuditCase] = []

    for case in raw.cases:
        if case.expected_role != "positive":
            continue
        occurrences: list[tuple[date, BenchmarkCandidateRecord]] = [
            (snapshot.cutoff_date, candidate)
            for snapshot in case.snapshots
            for candidate in snapshot.top_candidates
        ]
        grouped: dict[str, list[tuple[date, BenchmarkCandidateRecord]]] = defaultdict(list)
        for cutoff, candidate in occurrences:
            grouped[candidate.lineage_id or candidate.signal_id].append((cutoff, candidate))

        lineages = [
            _summarize_lineage(rows, alert_threshold=alert_threshold)
            for rows in grouped.values()
        ]
        lineages.sort(
            key=lambda row: (-row.best_target_score, -row.max_risk_score, row.first_date, row.lineage_id)
        )
        best = lineages[0] if lineages else None
        alerted = [row for row in lineages if row.ever_alerted]
        alerted.sort(
            key=lambda row: (-row.best_target_score, -row.max_risk_score, row.first_date, row.lineage_id)
        )
        best_alerted = alerted[0] if alerted else None
        any_target_matched_alert = any(
            row.ever_alerted and row.best_target_score >= target_match_threshold for row in lineages
        )

        caveats: list[str] = []
        if not case.benchmark_valid:
            caveats.append("Case was invalid in the source benchmark; no detector conclusion should be inferred.")
        if best is None:
            caveats.append("No candidate records were persisted for this positive case.")
        else:
            caveats.append(
                "Best target lineage means best among persisted top-N risk candidates plus alerts, not among every detector signal."
            )

        case_rows.append(
            TargetRiskAuditCase(
                case_id=case.case_id,
                name=case.name,
                campaign_number=case.campaign_number,
                detector_status=case.status,
                benchmark_valid=case.benchmark_valid,
                invalid_reason=case.invalid_reason,
                persisted_candidate_occurrences=len(occurrences),
                persisted_unique_lineages=len(lineages),
                any_alert=bool(alerted),
                any_target_matched_alert=any_target_matched_alert,
                best_persisted_target_score=best.best_target_score if best else None,
                best_persisted_target_lineage_id=best.lineage_id if best else None,
                best_persisted_target_lineage=best,
                best_alerted_lineage=best_alerted,
                top_target_lineages=tuple(lineages[:top_lineages]),
                diagnosis=_diagnosis(
                    case_status=case.status,
                    benchmark_valid=case.benchmark_valid,
                    best=best,
                    best_alerted=best_alerted,
                    target_match_threshold=target_match_threshold,
                ),
                caveats=tuple(caveats),
            )
        )

    return TargetRiskAuditResult(
        source_benchmark_run_id=raw.benchmark_run_id,
        source_freeze_id=raw.freeze_id,
        source_freeze_verified=raw.freeze_verified,
        source_lock_verified=raw.lock_verified,
        target_match_threshold=target_match_threshold,
        alert_threshold=round(alert_threshold, 2),
        positive_case_count=len(case_rows),
        valid_positive_case_count=sum(row.benchmark_valid for row in case_rows),
        invalid_positive_case_count=sum(not row.benchmark_valid for row in case_rows),
        cases=tuple(case_rows),
        caveats=(
            "Development diagnostic only: the source validation cohort is exposed and cannot serve as a fresh validation set.",
            "No detector, extraction, clustering, risk, recall matching, or embedding work is recomputed; this audit reads persisted raw benchmark candidates only.",
            "Raw benchmark snapshots persist the ordinary top-N risk candidates plus every alert, not every signal. Absence from this audit is not proof that no target-related signal existed.",
            "Weighted headroom ranks how much each risk factor could add if it were perfect; it is diagnostic, not a recommendation to tune that factor.",
        ),
    )
