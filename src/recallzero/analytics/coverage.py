from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from recallzero.models import Complaint

# Internal blend weights for the legacy single "affected coverage" score. These are fixed
# constants, not user-configurable risk weights. Detector v2 splits coverage into two
# independent risk factors (vehicle_coverage and model_year_breadth); the blended constants
# below are retained only for the backward-compatible `score` field.
_EVIDENCE_BLEND = 0.30
_MODEL_YEAR_BLEND = 0.30
_VIN_BLEND = 0.15
_VEHICLE_BLEND = 0.25

# Blend weights for the v2 vehicle_coverage sub-score (evidence + VIN-prefix + cross-vehicle
# breadth). Model-year breadth is intentionally excluded so it can stand alone as its own
# risk factor. Renormalized to sum to 1.0.
_VEHICLE_EVIDENCE_BLEND = 0.40
_VEHICLE_VIN_BLEND = 0.20
_VEHICLE_FLEET_BLEND = 0.40


@dataclass(slots=True)
class CoverageResult:
    """Deterministic breadth measurement for a complaint pattern.

    Exposes both the legacy blended ``score`` and the two Detector v2 sub-scores:
    ``vehicle_coverage_score`` (evidence volume + VIN-prefix + cross-vehicle breadth) and
    ``model_year_breadth_score`` (distinct model-year span alone).
    """

    score: float
    distinct_model_years: int
    model_year_span: int
    distinct_vin_prefixes: int
    vin_evidence_ratio: float
    distinct_vehicles: int
    explanation: str
    # Detector v2 split sub-scores.
    vehicle_coverage_score: float = 0.0
    model_year_breadth_score: float = 0.0


def _saturating(value: float, half_life: float) -> float:
    return 100.0 * (1.0 - math.exp(-max(0.0, value) / half_life))


def _model_year_breadth(distinct_model_years: int) -> float:
    return round(min(100.0, max(0.0, _saturating(max(0, distinct_model_years - 1), 2.0))), 2)


def _vehicle_coverage(*, evidence_count: int, distinct_vin_prefixes: int, distinct_vehicles: int) -> float:
    evidence_score = _saturating(evidence_count, 6.0)
    vin_score = _saturating(distinct_vin_prefixes, 4.0)
    vehicle_score = _saturating(max(0, distinct_vehicles - 1), 1.5)
    score = (
        _VEHICLE_EVIDENCE_BLEND * evidence_score
        + _VEHICLE_VIN_BLEND * vin_score
        + _VEHICLE_FLEET_BLEND * vehicle_score
    )
    return round(min(100.0, max(0.0, score)), 2)


def _blend(
    *, evidence_count: int, distinct_model_years: int, distinct_vin_prefixes: int, distinct_vehicles: int
) -> float:
    evidence_score = _saturating(evidence_count, 6.0)
    year_score = _saturating(max(0, distinct_model_years - 1), 2.0)
    vin_score = _saturating(distinct_vin_prefixes, 4.0)
    vehicle_score = _saturating(max(0, distinct_vehicles - 1), 1.5)
    score = (
        _EVIDENCE_BLEND * evidence_score
        + _MODEL_YEAR_BLEND * year_score
        + _VIN_BLEND * vin_score
        + _VEHICLE_BLEND * vehicle_score
    )
    return round(min(100.0, max(0.0, score)), 2)


class CoverageEngine:
    """Computes breadth factors: model-year breadth, VIN-prefix breadth, and (for the
    manufacturer-wide watch) cross-vehicle breadth.

    NHTSA public complaint data exposes only an 11-character VIN prefix (WMI + VDS +
    check digit + model year + plant code), never the full unique serial number.
    Distinct VIN-prefix counts are therefore a conservative lower bound on distinct
    physical vehicles, not an exact count, and are reported as such.
    """

    def calculate(self, complaints: Sequence[Complaint], *, distinct_vehicles: int = 1) -> CoverageResult:
        model_years = sorted(
            {complaint.vehicle.model_years[0] for complaint in complaints if complaint.vehicle.model_years}
        )
        vin_prefixes = {complaint.vin_prefix for complaint in complaints if complaint.vin_prefix}
        evidence_count = len(complaints)
        distinct_model_years = len(model_years)
        model_year_span = (model_years[-1] - model_years[0] + 1) if model_years else 0
        distinct_vin_prefixes = len(vin_prefixes)
        vin_evidence_ratio = round(distinct_vin_prefixes / evidence_count, 4) if evidence_count else 0.0

        score = _blend(
            evidence_count=evidence_count,
            distinct_model_years=distinct_model_years,
            distinct_vin_prefixes=distinct_vin_prefixes,
            distinct_vehicles=distinct_vehicles,
        )
        year_text = f"{model_years[0]}-{model_years[-1]}" if model_years else "unknown"
        vehicle_text = (
            f"{distinct_vehicles} distinct vehicle model(s) in the fleet share this issue"
            if distinct_vehicles > 1
            else "single-vehicle analysis (no cross-model comparison performed)"
        )
        explanation = (
            f"{evidence_count} complaint(s) span {distinct_model_years} distinct model year(s) ({year_text}); "
            f"{distinct_vin_prefixes} distinct VIN-prefix value(s) found ({vin_evidence_ratio:.0%} of complaints "
            "have usable VIN data, an 11-character prefix that is a conservative proxy for distinct vehicles, "
            f"not an exact unique-VIN count); {vehicle_text}."
        )
        return CoverageResult(
            score=score,
            distinct_model_years=distinct_model_years,
            model_year_span=model_year_span,
            distinct_vin_prefixes=distinct_vin_prefixes,
            vin_evidence_ratio=vin_evidence_ratio,
            distinct_vehicles=distinct_vehicles,
            explanation=explanation,
            vehicle_coverage_score=_vehicle_coverage(
                evidence_count=evidence_count,
                distinct_vin_prefixes=distinct_vin_prefixes,
                distinct_vehicles=distinct_vehicles,
            ),
            model_year_breadth_score=_model_year_breadth(distinct_model_years),
        )

    def rescale(
        self,
        *,
        evidence_count: int,
        distinct_model_years: int,
        distinct_vin_prefixes: int,
        vin_evidence_ratio: float,
        model_year_span: int,
        distinct_vehicles: int,
    ) -> CoverageResult:
        """Re-blend an already-computed coverage result with an updated cross-vehicle
        breadth count, without needing the original complaint records. Used by the
        manufacturer-wide watch to give credit when the same failure mechanism recurs
        across multiple distinct models in the lineup.
        """

        score = _blend(
            evidence_count=evidence_count,
            distinct_model_years=distinct_model_years,
            distinct_vin_prefixes=distinct_vin_prefixes,
            distinct_vehicles=distinct_vehicles,
        )
        explanation = (
            f"{evidence_count} complaint(s) span {distinct_model_years} distinct model year(s); "
            f"{distinct_vin_prefixes} distinct VIN-prefix value(s) ({vin_evidence_ratio:.0%} of complaints); "
            f"{distinct_vehicles} distinct vehicle model(s) in the fleet share this issue."
        )
        return CoverageResult(
            score=score,
            distinct_model_years=distinct_model_years,
            model_year_span=model_year_span,
            distinct_vin_prefixes=distinct_vin_prefixes,
            vin_evidence_ratio=vin_evidence_ratio,
            distinct_vehicles=distinct_vehicles,
            explanation=explanation,
            vehicle_coverage_score=_vehicle_coverage(
                evidence_count=evidence_count,
                distinct_vin_prefixes=distinct_vin_prefixes,
                distinct_vehicles=distinct_vehicles,
            ),
            model_year_breadth_score=_model_year_breadth(distinct_model_years),
        )