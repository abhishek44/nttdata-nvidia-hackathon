from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field

from recallzero.detector_v2 import DetectorV2
from recallzero.models import Vehicle
from recallzero.models.domain import StrictModel


class FleetRow(StrictModel):
    vehicle: str
    issue: str
    final_score: float
    level: str
    evidence_count: int
    distinct_vehicles: int
    campaign_number: str | None = None


class FleetDashboard(StrictModel):
    manufacturer: str
    rows: tuple[FleetRow, ...]
    vehicle_count: int = 0
    notes: tuple[str, ...] = Field(default_factory=tuple)


def load_manufacturer_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return raw


async def run_fleet_watch(
    detector: DetectorV2,
    *,
    config_path: Path,
    refresh: bool = False,
    max_complaints: int | None = None,
) -> FleetDashboard:
    """Run Detector v2 across a manufacturer lineup and rescore cross-vehicle recurrence.

    Roadmap capability: demonstrates how the single-vehicle analysis scales to a
    manufacturer radar. Per-vehicle analysis reuses the frozen evidence pipeline; a
    lightweight second pass groups signals across vehicles by failure mechanism and, for
    any mechanism appearing in >= 2 distinct models, rescales the vehicle_coverage factor
    via CoverageEngine.rescale() and recomputes the v2 risk without rerunning extraction.
    """
    config = load_manufacturer_config(config_path)
    manufacturer = str(config.get("manufacturer", "Unknown"))
    vehicles = [
        Vehicle(make=item["make"], model=item["model"], model_years=tuple(item["years"]))
        for item in config.get("vehicles", [])
    ]

    # mechanism -> list of (vehicle_display, signal_v2)
    mechanism_index: dict[str, list[tuple[str, Any]]] = defaultdict(list)
    notes: list[str] = []
    per_vehicle_results = []
    for vehicle in vehicles:
        try:
            result = await detector.analyze_vehicle(
                vehicle, refresh=refresh, max_complaints=max_complaints
            )
            per_vehicle_results.append(result)
            for signal_v2 in result.signals_v2:
                mechanism = signal_v2.signal.cluster.failure_mechanism
                mechanism_index[mechanism].append((vehicle.display_name, signal_v2))
        except Exception as exc:  # keep the rest of the fleet running on one failure
            notes.append(f"{vehicle.display_name}: analysis failed ({exc}); skipped.")

    # Count distinct (make, model) pairs sharing each failure mechanism.
    mechanism_vehicle_count = {
        mechanism: len({display for display, _ in items})
        for mechanism, items in mechanism_index.items()
    }

    rows: list[FleetRow] = []
    for result in per_vehicle_results:
        for signal_v2 in result.signals_v2:
            signal = signal_v2.signal
            mechanism = signal.cluster.failure_mechanism
            distinct_vehicles = mechanism_vehicle_count.get(mechanism, 1)
            final_score = signal_v2.risk.final_score
            level = signal_v2.risk.level.value
            if distinct_vehicles >= 2:
                # Rescale vehicle_coverage to credit cross-model recurrence, then recompute
                # the v2 risk deterministically (no re-extraction, no re-clustering).
                rescaled = detector.coverage_engine.rescale(
                    evidence_count=signal.cluster.evidence_count,
                    distinct_model_years=signal_v2.coverage.distinct_model_years,
                    distinct_vin_prefixes=signal_v2.coverage.distinct_vin_prefixes,
                    vin_evidence_ratio=signal_v2.coverage.vin_evidence_ratio,
                    model_year_span=signal_v2.coverage.model_year_span,
                    distinct_vehicles=distinct_vehicles,
                )
                risk = detector.risk_engine.calculate(
                    severity=detector._severity_stub(signal),
                    trend=signal.trend,
                    recall_match=signal.recall_match,
                    coverage=rescaled,
                    evidence_count=signal.cluster.evidence_count,
                )
                final_score = risk.final_score
                level = risk.level.value
            rows.append(
                FleetRow(
                    vehicle=signal.vehicle.display_name,
                    issue=signal.cluster.label,
                    final_score=final_score,
                    level=level,
                    evidence_count=signal.cluster.evidence_count,
                    distinct_vehicles=distinct_vehicles,
                    campaign_number=signal.recall_match.campaign_number,
                )
            )

    rows.sort(key=lambda row: (-row.final_score, row.vehicle, row.issue))
    return FleetDashboard(
        manufacturer=manufacturer,
        rows=tuple(rows),
        vehicle_count=len(vehicles),
        notes=tuple(notes),
    )