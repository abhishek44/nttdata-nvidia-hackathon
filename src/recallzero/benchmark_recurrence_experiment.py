from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import median
from pydantic import BaseModel, Field

from recallzero.benchmark import BenchmarkCandidateRecord, BenchmarkCaseResult, BenchmarkRunResult


class RecurrenceGateSpec(BaseModel):
    name: str
    min_growth_months: int
    min_growth_span_days: int
    min_max_evidence: int
    min_evidence_gain_after_first: int


class RecurrenceGateResult(BaseModel):
    name: str
    passed: bool
    min_growth_months: int
    min_growth_span_days: int
    min_max_evidence: int
    min_evidence_gain_after_first: int


class LineageRecurrenceMetrics(BaseModel):
    lineage_id: str
    signal_scope: str
    representative_issue: str
    selection_reason: str
    persisted_occurrence_count: int
    first_persisted_date: date
    last_persisted_date: date
    persisted_span_days: int
    first_evidence_count: int
    max_evidence_count: int
    evidence_gain_after_first: int
    evidence_growth_event_count: int
    evidence_growth_month_count: int
    evidence_growth_span_days: int
    evidence_growth_dates: tuple[date, ...] = Field(default_factory=tuple)
    max_risk_score: float
    max_threshold_margin: float
    ever_alerted: bool
    best_target_score: float | None = None
    target_match_qualified: bool | None = None
    recurrence_gates: tuple[RecurrenceGateResult, ...] = Field(default_factory=tuple)


class RecurrenceCaseRow(BaseModel):
    case_id: str
    name: str
    expected_role: str
    benchmark_valid: bool
    invalid_reason: str | None = None
    detector_status: str
    campaign_number: str | None = None
    selected_lineages: tuple[LineageRecurrenceMetrics, ...] = Field(default_factory=tuple)
    caveats: tuple[str, ...] = Field(default_factory=tuple)


class GateSeparationSummary(BaseModel):
    gate_name: str
    valid_positive_count: int
    positive_pass_count: int
    positive_pass_rate: float | None
    target_matched_positive_count: int
    target_matched_positive_pass_count: int
    target_matched_positive_pass_rate: float | None
    valid_control_count: int
    control_pass_count: int
    control_pass_rate: float | None
    positive_minus_control_rate: float | None
    target_matched_positive_minus_control_rate: float | None


class MetricSeparationSummary(BaseModel):
    metric: str
    positive_median: float | None
    target_matched_positive_median: float | None
    control_median: float | None
    positive_vs_control_pairwise_auc: float | None
    target_matched_positive_vs_control_pairwise_auc: float | None


class RecurrenceSeparationExperimentResult(BaseModel):
    experiment_id: str = "long-horizon-recurrence-experiment-a"
    scope: str = "development_diagnostic_only"
    source_benchmark_run_id: str
    source_freeze_id: str
    source_freeze_verified: bool
    source_lock_verified: bool
    target_match_threshold: float
    detector_recomputed: bool = False
    risk_recomputed: bool = False
    llm_calls_required: int = 0
    embedding_calls_required: int = 0
    persisted_candidate_scope: str = "top_n_by_risk_plus_all_alerts"
    positive_selection: str = "best_posthoc_target_lineage_per_valid_positive"
    control_selection: str = "highest_max_risk_lineage_per_valid_control"
    gate_specs: tuple[RecurrenceGateSpec, ...]
    valid_positive_case_count: int
    positive_lineage_available_count: int
    target_matched_positive_case_count: int
    valid_control_case_count: int
    invalid_positive_case_count: int
    invalid_control_case_count: int
    cases: tuple[RecurrenceCaseRow, ...]
    gate_separation: tuple[GateSeparationSummary, ...]
    metric_separation: tuple[MetricSeparationSummary, ...]
    caveats: tuple[str, ...] = Field(default_factory=tuple)


DEFAULT_RECURRENCE_GATES: tuple[RecurrenceGateSpec, ...] = (
    RecurrenceGateSpec(
        name="LHREC_PRIMARY_90D_3M_4E",
        min_growth_months=3,
        min_growth_span_days=90,
        min_max_evidence=4,
        min_evidence_gain_after_first=2,
    ),
    RecurrenceGateSpec(
        name="LHREC_MODERATE_120D_4M_5E",
        min_growth_months=4,
        min_growth_span_days=120,
        min_max_evidence=5,
        min_evidence_gain_after_first=3,
    ),
    RecurrenceGateSpec(
        name="LHREC_STRICT_180D_5M_6E",
        min_growth_months=5,
        min_growth_span_days=180,
        min_max_evidence=6,
        min_evidence_gain_after_first=4,
    ),
)


def _lineage_key(candidate: BenchmarkCandidateRecord) -> str:
    return candidate.lineage_id or candidate.signal_id


def _dedupe_occurrences(
    occurrences: list[tuple[date, BenchmarkCandidateRecord]],
) -> list[tuple[date, BenchmarkCandidateRecord]]:
    """Keep one conservative representative per cutoff for a lineage.

    Candidate persistence in the raw benchmark is weekly. The recurrence experiment
    must not count repeated weekly persistence as new evidence, so recurrence is based
    only on increases in the running maximum evidence count.
    """
    by_date: dict[date, BenchmarkCandidateRecord] = {}
    for cutoff, candidate in occurrences:
        current = by_date.get(cutoff)
        if current is None or (
            candidate.evidence_count,
            candidate.risk_score,
            candidate.posthoc_target_score or -1.0,
        ) > (
            current.evidence_count,
            current.risk_score,
            current.posthoc_target_score or -1.0,
        ):
            by_date[cutoff] = candidate
    return sorted(by_date.items(), key=lambda row: row[0])


def _gate_results(
    *,
    growth_months: int,
    growth_span_days: int,
    max_evidence: int,
    evidence_gain_after_first: int,
    gates: tuple[RecurrenceGateSpec, ...],
) -> tuple[RecurrenceGateResult, ...]:
    return tuple(
        RecurrenceGateResult(
            name=gate.name,
            passed=(
                growth_months >= gate.min_growth_months
                and growth_span_days >= gate.min_growth_span_days
                and max_evidence >= gate.min_max_evidence
                and evidence_gain_after_first >= gate.min_evidence_gain_after_first
            ),
            min_growth_months=gate.min_growth_months,
            min_growth_span_days=gate.min_growth_span_days,
            min_max_evidence=gate.min_max_evidence,
            min_evidence_gain_after_first=gate.min_evidence_gain_after_first,
        )
        for gate in gates
    )


def _summarize_recurrence(
    occurrences: list[tuple[date, BenchmarkCandidateRecord]],
    *,
    selection_reason: str,
    target_match_threshold: float,
    gates: tuple[RecurrenceGateSpec, ...],
) -> LineageRecurrenceMetrics:
    rows = _dedupe_occurrences(occurrences)
    if not rows:
        raise ValueError("Cannot summarize an empty lineage")

    first_date, first_candidate = rows[0]
    last_date = rows[-1][0]
    first_evidence = int(first_candidate.evidence_count)

    running_max = -1
    growth_dates: list[date] = []
    for cutoff, candidate in rows:
        evidence = int(candidate.evidence_count)
        if evidence > running_max:
            growth_dates.append(cutoff)
            running_max = evidence

    max_evidence = max(int(candidate.evidence_count) for _, candidate in rows)
    growth_months = len({(value.year, value.month) for value in growth_dates})
    growth_span_days = (
        (growth_dates[-1] - growth_dates[0]).days if len(growth_dates) >= 2 else 0
    )
    best_target_score = max(
        (
            float(candidate.posthoc_target_score)
            for _, candidate in rows
            if candidate.posthoc_target_score is not None
        ),
        default=None,
    )
    max_risk = max(rows, key=lambda row: (row[1].risk_score, row[1].evidence_count, -row[0].toordinal()))[1]
    issue_candidate = max(
        rows,
        key=lambda row: (
            row[1].posthoc_target_score if row[1].posthoc_target_score is not None else -1.0,
            row[1].risk_score,
        ),
    )[1]

    return LineageRecurrenceMetrics(
        lineage_id=_lineage_key(max_risk),
        signal_scope=max_risk.signal_scope,
        representative_issue=issue_candidate.issue,
        selection_reason=selection_reason,
        persisted_occurrence_count=len(rows),
        first_persisted_date=first_date,
        last_persisted_date=last_date,
        persisted_span_days=(last_date - first_date).days,
        first_evidence_count=first_evidence,
        max_evidence_count=max_evidence,
        evidence_gain_after_first=max(0, max_evidence - first_evidence),
        evidence_growth_event_count=len(growth_dates),
        evidence_growth_month_count=growth_months,
        evidence_growth_span_days=growth_span_days,
        evidence_growth_dates=tuple(growth_dates),
        max_risk_score=round(max(candidate.risk_score for _, candidate in rows), 2),
        max_threshold_margin=round(max(candidate.threshold_margin for _, candidate in rows), 2),
        ever_alerted=any(candidate.alert for _, candidate in rows),
        best_target_score=round(best_target_score, 4) if best_target_score is not None else None,
        target_match_qualified=(
            best_target_score >= target_match_threshold if best_target_score is not None else None
        ),
        recurrence_gates=_gate_results(
            growth_months=growth_months,
            growth_span_days=growth_span_days,
            max_evidence=max_evidence,
            evidence_gain_after_first=max(0, max_evidence - first_evidence),
            gates=gates,
        ),
    )


def _group_case_lineages(
    case: BenchmarkCaseResult,
) -> dict[str, list[tuple[date, BenchmarkCandidateRecord]]]:
    grouped: dict[str, list[tuple[date, BenchmarkCandidateRecord]]] = defaultdict(list)
    for snapshot in case.snapshots:
        for candidate in snapshot.top_candidates:
            grouped[_lineage_key(candidate)].append((snapshot.cutoff_date, candidate))
    return grouped


def _best_positive_lineage(
    grouped: dict[str, list[tuple[date, BenchmarkCandidateRecord]]],
    *,
    target_match_threshold: float,
    gates: tuple[RecurrenceGateSpec, ...],
) -> LineageRecurrenceMetrics | None:
    rows = [
        _summarize_recurrence(
            occurrences,
            selection_reason="best_posthoc_target_lineage",
            target_match_threshold=target_match_threshold,
            gates=gates,
        )
        for occurrences in grouped.values()
    ]
    if not rows:
        return None
    rows.sort(
        key=lambda row: (
            -(row.best_target_score if row.best_target_score is not None else -1.0),
            -row.max_risk_score,
            row.first_persisted_date,
            row.lineage_id,
        )
    )
    return rows[0]


def _top_control_lineages(
    grouped: dict[str, list[tuple[date, BenchmarkCandidateRecord]]],
    *,
    target_match_threshold: float,
    gates: tuple[RecurrenceGateSpec, ...],
    count: int,
) -> tuple[LineageRecurrenceMetrics, ...]:
    rows = [
        _summarize_recurrence(
            occurrences,
            selection_reason="highest_max_risk_control_lineage",
            target_match_threshold=target_match_threshold,
            gates=gates,
        )
        for occurrences in grouped.values()
    ]
    rows.sort(
        key=lambda row: (
            -row.max_risk_score,
            -row.max_evidence_count,
            -row.evidence_growth_month_count,
            row.first_persisted_date,
            row.lineage_id,
        )
    )
    return tuple(rows[:count])


def _gate_pass(lineage: LineageRecurrenceMetrics, gate_name: str) -> bool:
    return any(result.name == gate_name and result.passed for result in lineage.recurrence_gates)


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _median(values: list[float]) -> float | None:
    return round(float(median(values)), 4) if values else None


def _pairwise_auc(positive: list[float], negative: list[float]) -> float | None:
    """Probability that a random positive has a larger value than a random control.

    Ties count as 0.5. This is a descriptive development statistic, not an inferential
    claim, and avoids fitting a threshold to the exposed cohort.
    """
    if not positive or not negative:
        return None
    wins = 0.0
    total = 0
    for pos in positive:
        for neg in negative:
            total += 1
            if pos > neg:
                wins += 1.0
            elif pos == neg:
                wins += 0.5
    return round(wins / total, 4)


def _metric_value(lineage: LineageRecurrenceMetrics, metric: str) -> float:
    return float(getattr(lineage, metric))


def _build_gate_separation(
    *,
    gates: tuple[RecurrenceGateSpec, ...],
    positives: list[LineageRecurrenceMetrics],
    valid_positive_count: int,
    controls: list[tuple[LineageRecurrenceMetrics, ...]],
) -> tuple[GateSeparationSummary, ...]:
    matched = [row for row in positives if row.target_match_qualified]
    results: list[GateSeparationSummary] = []
    for gate in gates:
        positive_pass = sum(_gate_pass(row, gate.name) for row in positives)
        matched_pass = sum(_gate_pass(row, gate.name) for row in matched)
        # A control case passes if any selected near-threshold lineage passes.
        control_pass = sum(
            any(_gate_pass(row, gate.name) for row in case_lineages)
            for case_lineages in controls
        )
        pos_rate = _rate(positive_pass, valid_positive_count)
        matched_rate = _rate(matched_pass, len(matched))
        control_rate = _rate(control_pass, len(controls))
        results.append(
            GateSeparationSummary(
                gate_name=gate.name,
                valid_positive_count=valid_positive_count,
                positive_pass_count=positive_pass,
                positive_pass_rate=pos_rate,
                target_matched_positive_count=len(matched),
                target_matched_positive_pass_count=matched_pass,
                target_matched_positive_pass_rate=matched_rate,
                valid_control_count=len(controls),
                control_pass_count=control_pass,
                control_pass_rate=control_rate,
                positive_minus_control_rate=(
                    round(pos_rate - control_rate, 4)
                    if pos_rate is not None and control_rate is not None
                    else None
                ),
                target_matched_positive_minus_control_rate=(
                    round(matched_rate - control_rate, 4)
                    if matched_rate is not None and control_rate is not None
                    else None
                ),
            )
        )
    return tuple(results)


def _build_metric_separation(
    *,
    positives: list[LineageRecurrenceMetrics],
    controls: list[tuple[LineageRecurrenceMetrics, ...]],
) -> tuple[MetricSeparationSummary, ...]:
    matched = [row for row in positives if row.target_match_qualified]
    # For continuous separation, use the highest-risk selected lineage per control
    # (the first row), preserving one independent observation per vehicle/control case.
    control_primary = [rows[0] for rows in controls if rows]
    metrics = (
        "evidence_growth_month_count",
        "evidence_growth_span_days",
        "evidence_growth_event_count",
        "evidence_gain_after_first",
        "max_evidence_count",
    )
    summaries: list[MetricSeparationSummary] = []
    for metric in metrics:
        pos_values = [_metric_value(row, metric) for row in positives]
        matched_values = [_metric_value(row, metric) for row in matched]
        control_values = [_metric_value(row, metric) for row in control_primary]
        summaries.append(
            MetricSeparationSummary(
                metric=metric,
                positive_median=_median(pos_values),
                target_matched_positive_median=_median(matched_values),
                control_median=_median(control_values),
                positive_vs_control_pairwise_auc=_pairwise_auc(pos_values, control_values),
                target_matched_positive_vs_control_pairwise_auc=_pairwise_auc(
                    matched_values, control_values
                ),
            )
        )
    return tuple(summaries)


def build_recurrence_separation_experiment(
    *,
    raw_result_path: Path,
    target_match_threshold: float = 0.45,
    control_lineages_per_case: int = 1,
    gates: tuple[RecurrenceGateSpec, ...] = DEFAULT_RECURRENCE_GATES,
) -> RecurrenceSeparationExperimentResult:
    """Compare slow-burn recurrence in target-positive and near-threshold control lineages.

    This is an offline development diagnostic. It does not modify or rerun Detector v1.
    Positive lineage selection uses post-hoc target attribution, so its output must never
    feed live detector scoring. Control selection uses only the highest frozen risk.
    """
    raw = BenchmarkRunResult.model_validate_json(raw_result_path.read_text(encoding="utf-8"))
    if not raw.freeze_verified or not raw.lock_verified:
        raise RuntimeError(
            "Long-Horizon Recurrence Experiment requires a freeze-verified and lock-verified source benchmark"
        )
    if not (0.0 <= target_match_threshold <= 1.0):
        raise ValueError("target_match_threshold must be between 0 and 1")
    if control_lineages_per_case < 1:
        raise ValueError("control_lineages_per_case must be at least 1")
    if not gates:
        raise ValueError("At least one recurrence gate is required")

    case_rows: list[RecurrenceCaseRow] = []
    valid_positive_lineages: list[LineageRecurrenceMetrics] = []
    valid_control_lineages: list[tuple[LineageRecurrenceMetrics, ...]] = []
    valid_positive_count = 0
    invalid_positive = 0
    invalid_control = 0

    for case in raw.cases:
        grouped = _group_case_lineages(case)
        caveats: list[str] = []
        selected: tuple[LineageRecurrenceMetrics, ...] = ()

        if case.expected_role == "positive":
            if not case.benchmark_valid:
                invalid_positive += 1
                caveats.append("Invalid source positive excluded from separation statistics.")
            else:
                valid_positive_count += 1
                best = _best_positive_lineage(
                    grouped,
                    target_match_threshold=target_match_threshold,
                    gates=gates,
                )
                if best is not None:
                    selected = (best,)
                    valid_positive_lineages.append(best)
                else:
                    caveats.append("No persisted positive candidate lineage was available.")
        else:
            if not case.benchmark_valid:
                invalid_control += 1
                caveats.append("Invalid source control excluded from separation statistics.")
            else:
                selected = _top_control_lineages(
                    grouped,
                    target_match_threshold=target_match_threshold,
                    gates=gates,
                    count=control_lineages_per_case,
                )
                valid_control_lineages.append(selected)
                if not selected:
                    caveats.append("No persisted control candidate lineage was available.")

        case_rows.append(
            RecurrenceCaseRow(
                case_id=case.case_id,
                name=case.name,
                expected_role=case.expected_role,
                benchmark_valid=case.benchmark_valid,
                invalid_reason=case.invalid_reason,
                detector_status=case.status,
                campaign_number=case.campaign_number,
                selected_lineages=selected,
                caveats=tuple(caveats),
            )
        )

    return RecurrenceSeparationExperimentResult(
        source_benchmark_run_id=raw.benchmark_run_id,
        source_freeze_id=raw.freeze_id,
        source_freeze_verified=raw.freeze_verified,
        source_lock_verified=raw.lock_verified,
        target_match_threshold=target_match_threshold,
        gate_specs=gates,
        valid_positive_case_count=valid_positive_count,
        positive_lineage_available_count=len(valid_positive_lineages),
        target_matched_positive_case_count=sum(
            bool(row.target_match_qualified) for row in valid_positive_lineages
        ),
        valid_control_case_count=len(valid_control_lineages),
        invalid_positive_case_count=invalid_positive,
        invalid_control_case_count=invalid_control,
        cases=tuple(case_rows),
        gate_separation=_build_gate_separation(
            gates=gates,
            positives=valid_positive_lineages,
            valid_positive_count=valid_positive_count,
            controls=valid_control_lineages,
        ),
        metric_separation=_build_metric_separation(
            positives=valid_positive_lineages,
            controls=valid_control_lineages,
        ),
        caveats=(
            "Development diagnostic only: the source validation cohort is exposed and cannot serve as a fresh validation set.",
            "Positive lineage selection uses post-hoc target recall information only for offline diagnosis; it must never feed live Detector v1/v2 scoring.",
            "Control lineage selection uses the highest frozen max-risk lineage per valid control case to stress-test false-alert separation.",
            "Recurrence counts only dates where the running maximum independent evidence count increases; repeated weekly persistence without new evidence is not counted as recurrence.",
            "Raw benchmark snapshots persist top-N-by-risk candidates plus every alert, not every detector signal. Absence from this experiment is not proof that no target-related signal existed.",
            "Because a lineage may first enter the persisted top-N after several complaints already exist, evidence-growth month counts are conservative and may undercount earlier recurrence.",
            "Gate thresholds are fixed screening definitions for this development experiment, not tuned Detector v2 parameters.",
        ),
    )
