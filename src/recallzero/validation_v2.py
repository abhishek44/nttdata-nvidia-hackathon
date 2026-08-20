from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from pydantic import Field

from recallzero.detector_v2 import DetectorV2
from recallzero.models import Vehicle
from recallzero.models.domain import StrictModel

_V1_BASELINE_SENSITIVITY = "1/10 (10%) - Detector v1 honest validation result, preserved"


class ValidationRow(StrictModel):
    name: str
    expected_role: str
    status: str
    detected: str
    detail: str = ""


class ValidationReport(StrictModel):
    total_cases: int
    valid_cases: int
    positive_total: int
    positive_detected: int
    positive_sensitivity: float
    control_total: int
    control_alerted: int
    rows: tuple[ValidationRow, ...]
    notes: tuple[str, ...] = Field(default_factory=tuple)


def _load_cases(cases_path: Path):
    """Load the locked 20-case cohort read-only via the frozen benchmark module.

    candidates.yml and its lock file are untouched; this only parses the YAML so the
    v2 detector is challenged against the exact same cases as Detector v1.
    """
    from recallzero.benchmark import load_candidates

    return load_candidates(cases_path)


async def _detect_case(
    detector: DetectorV2,
    case,
    *,
    refresh: bool,
) -> tuple[bool, str]:
    """Replay one case through Detector v2 with a cutoff just before the recall date.

    For positives, 'detected' means any v2 alert fired at the pre-recall cutoff. For
    controls, 'detected' means any v2 alert fired (counted toward the control alert
    burden, reported honestly). The recall-match gate uses only recalls visible at the
    cutoff, matching Detector v1's leakage rules.
    """
    vehicle = Vehicle(make=case.make, model=case.model, model_years=case.model_years)
    if case.expected_role == "positive" and case.official_recall_date is not None:
        cutoff = case.official_recall_date - timedelta(days=1)
    else:
        cutoff = None  # control: analyze at latest available data
    result = await detector.analyze_vehicle(vehicle, cutoff_date=cutoff, refresh=refresh)
    alerted = any(item.risk.alert for item in result.signals_v2)
    top = result.signals_v2[0] if result.signals_v2 else None
    detail = (
        f"top={top.risk.final_score:.1f} ({top.risk.level.value})"
        if top
        else "no signals"
    )
    return alerted, detail


async def run_validation(
    detector: DetectorV2,
    *,
    cases_path: Path,
    refresh: bool = False,
) -> ValidationReport:
    cases = _load_cases(cases_path)
    rows: list[ValidationRow] = []
    positive_total = positive_detected = control_total = control_alerted = valid = 0
    notes: list[str] = [
        f"Detector v1 baseline sensitivity: {_V1_BASELINE_SENSITIVITY}.",
        "Gates are the same as Detector v1: positive sensitivity and control alert "
        "discipline are both reported; control inflation is surfaced, not hidden.",
    ]
    for case in cases:
        try:
            alerted, detail = await _detect_case(detector, case, refresh=refresh)
            status = "VALID"
            valid += 1
        except Exception as exc:  # fail-closed per case, matching v1's benchmark posture
            alerted, status, detail = False, "INVALID", f"{type(exc).__name__}: {exc}"
        is_positive = case.expected_role == "positive"
        if is_positive:
            positive_total += 1
            positive_detected += int(alerted)
        else:
            control_total += 1
            control_alerted += int(alerted)
        rows.append(
            ValidationRow(
                name=case.name,
                expected_role=case.expected_role,
                status=status,
                detected=("ALERT" if alerted else "quiet") if status == "VALID" else "-",
                detail=detail,
            )
        )
    sensitivity = (positive_detected / positive_total) if positive_total else 0.0
    return ValidationReport(
        total_cases=len(cases),
        valid_cases=valid,
        positive_total=positive_total,
        positive_detected=positive_detected,
        positive_sensitivity=round(sensitivity, 4),
        control_total=control_total,
        control_alerted=control_alerted,
        rows=tuple(rows),
        notes=tuple(notes),
    )