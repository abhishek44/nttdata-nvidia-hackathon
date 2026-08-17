from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from recallzero.backtest import RecallTimeMachine
from recallzero.config import Settings
from recallzero.investigation import EngineeringBriefRenderer
from recallzero.models import Vehicle
from recallzero.pipeline import build_pipeline
from recallzero.utils import dumps_json


class VehicleToolInput(BaseModel):
    make: str = Field(description="Vehicle manufacturer, for example FORD")
    model: str = Field(description="Vehicle model, for example MUSTANG MACH-E")
    model_years: list[int] = Field(description="One or more model years", min_length=1)

    def vehicle(self) -> Vehicle:
        return Vehicle(make=self.make, model=self.model, model_years=tuple(self.model_years))


class AnalyzeVehicleToolInput(VehicleToolInput):
    cutoff_date: date | None = Field(default=None, description="Evidence cutoff in YYYY-MM-DD format; defaults to today")
    refresh: bool = Field(default=False, description="Fetch fresh NHTSA data instead of using the local cache")
    max_complaints: int | None = Field(default=None, ge=1, le=10000)


class BacktestToolInput(VehicleToolInput):
    target_campaign_number: str = Field(description="NHTSA campaign number, for example 22V412000")
    official_recall_date: date = Field(description="Official recall boundary in YYYY-MM-DD format")
    replay_start_date: date | None = Field(default=None, description="Optional first weekly replay date")
    alert_threshold: float | None = Field(default=None, ge=0, le=100)
    minimum_evidence: int | None = Field(default=None, ge=1)
    refresh: bool = False

    @field_validator("target_campaign_number")
    @classmethod
    def clean_campaign(cls, value: str) -> str:
        return value.upper().replace("-", "").strip()


class EvidenceToolInput(BaseModel):
    odi_number: str = Field(description="NHTSA ODI complaint identifier")


class BriefToolInput(BaseModel):
    run_id: str
    signal_id: str


def settings_for_tool(data_dir: str, use_nim: bool) -> Settings:
    return Settings(data_dir=Path(data_dir), use_nim=use_nim)


def analysis_tool_payload(run) -> dict[str, Any]:
    """Compact, metric-preserving payload for agent consumption.

    Large evidence arrays are intentionally truncated. The agent can call
    ``recallzero_get_evidence`` for specific ODI identifiers instead of receiving
    hundreds of complaint IDs and narratives in its context window.
    """

    diagnostics = run.clustering_diagnostics or {}
    component_groups = diagnostics.get("component_groups") or {}
    compact_diagnostics = {
        "algorithm": diagnostics.get("algorithm"),
        "eps": diagnostics.get("eps"),
        "min_samples": diagnostics.get("min_samples"),
        "largest_cluster_share": diagnostics.get("largest_cluster_share"),
        "suspicious_single_cluster": diagnostics.get("suspicious_single_cluster"),
        "suspicious_dominant_cluster": diagnostics.get("suspicious_dominant_cluster"),
        "component_groups": {
            name: {
                "count": values.get("count"),
                "dbscan_clusters": values.get("dbscan_clusters"),
                "noise_points": values.get("noise_points"),
            }
            for name, values in sorted(component_groups.items())
        },
        "largest_cluster_sizes": list(diagnostics.get("cluster_sizes") or [])[:12],
    }

    return {
        "run_id": run.run_id,
        "vehicle": run.vehicle.model_dump(mode="json"),
        "cutoff_date": run.cutoff_date.isoformat(),
        "complaint_count": run.complaint_count,
        "recall_count_visible": run.recall_count_visible,
        "cluster_count": run.cluster_count,
        "meta_signal_count": run.meta_signal_count,
        "extraction_methods": run.extraction_method_counts,
        "embedding_method": run.embedding_method.value,
        "semantic_quality": run.semantic_quality,
        "clustering_diagnostics": compact_diagnostics,
        "agent_grounding_rules": [
            "Use only returned deterministic values for counts, dates, windows, trends, persistence, risk, recall similarity, and lead time.",
            "Preserve metric names: trend_ratio is recent-rate divided by baseline-rate, not week-over-week unless the returned window is seven days.",
            "Risk is an engineering-prioritization score and does not prove a defect.",
            "A low or absent recall similarity does not establish a recall coverage gap.",
            "Owner statements about recall applicability remain owner-reported allegations unless independently verified by a RecallZero recall-scope tool.",
            "Crash, fire, injury, and component fields do not by themselves establish causation.",
            "If semantic_quality is DEGRADED or clustering diagnostics are suspicious, describe cluster labels and downstream conclusions as provisional.",
            "Prefer representative_evidence_ids for ODI drill-down and do not request every complaint record.",
        ],
        "metric_definitions": {
            "recent_count": "Complaint records in the configured recent window for this cluster, not necessarily one week.",
            "baseline_count": "Complaint records in the configured preceding baseline window for this cluster.",
            "trend_ratio": "Smoothed recent complaint rate divided by baseline complaint rate; do not call it week-over-week.",
            "persistence_weeks": "Maximum consecutive active-week run within the recent eight-week persistence horizon.",
            "active_weeks_recent_4": "Number of active weeks in the most recent four-week horizon.",
            "risk_score": "Deterministic prioritization score for engineering investigation; it is not proof of a defect.",
            "recall_match": "Text/component similarity against recalls visible at the cutoff; a low score does not establish a recall coverage gap.",
        },
        "warnings": list(run.warnings),
        "signals": [
            {
                "signal_id": signal.signal_id,
                "lineage_id": signal.lineage_id,
                "signal_scope": signal.signal_scope,
                "issue": signal.cluster.label,
                "failure_mechanism": signal.cluster.failure_mechanism,
                "consequence_family": signal.cluster.consequence_family,
                "alert": signal.risk.alert,
                "risk_level": signal.risk.level.value,
                "risk_score": signal.risk.final_score,
                "evidence_count": signal.cluster.evidence_count,
                "recent_count": signal.trend.recent_count,
                "recent_window_days": signal.trend.recent_window_days,
                "baseline_count": signal.trend.baseline_count,
                "baseline_window_days": signal.trend.baseline_window_days,
                "recent_rate_per_28d": signal.trend.recent_rate_per_28d,
                "baseline_rate_per_28d": signal.trend.baseline_rate_per_28d,
                "trend_ratio": signal.trend.trend_ratio,
                "persistence_weeks": signal.trend.persistence_weeks,
                "active_weeks_recent_4": signal.trend.active_weeks_recent_4,
                "max_consecutive_weeks_recent_8": signal.trend.max_consecutive_weeks_recent_8,
                "recall_match": signal.recall_match.model_dump(mode="json"),
                "rationale": signal.risk.rationale,
                "representative_evidence_ids": list(signal.cluster.representative_complaint_ids),
                "evidence_ids": [item.complaint_id for item in signal.evidence[:12]],
                "evidence_ids_truncated": len(signal.evidence) > 12,
            }
            for signal in run.signals[:12]
        ],
    }


async def execute_fetch(input_data: VehicleToolInput, *, data_dir: str, use_nim: bool) -> str:
    pipeline = build_pipeline(settings_for_tool(data_dir, use_nim), use_nim=use_nim)
    complaints, recalls = await pipeline.ingest(input_data.vehicle(), refresh=False)
    payload = {
        "vehicle": input_data.vehicle().model_dump(mode="json"),
        "complaint_count": len(complaints),
        "recall_count": len(recalls),
        "earliest_complaint": min((item.received_date for item in complaints), default=None),
        "latest_complaint": max((item.received_date for item in complaints), default=None),
        "cache_path": str(pipeline.repository.complaints_path(input_data.vehicle())),
    }
    return dumps_json(payload, indent=None)


async def execute_analyze(input_data: AnalyzeVehicleToolInput, *, data_dir: str, use_nim: bool) -> str:
    pipeline = build_pipeline(settings_for_tool(data_dir, use_nim), use_nim=use_nim)
    run = await pipeline.analyze_vehicle(
        input_data.vehicle(),
        cutoff_date=input_data.cutoff_date,
        refresh=input_data.refresh,
        max_complaints=input_data.max_complaints,
    )
    return dumps_json(analysis_tool_payload(run), indent=None)


async def execute_backtest(input_data: BacktestToolInput, *, data_dir: str, use_nim: bool) -> str:
    pipeline = build_pipeline(settings_for_tool(data_dir, use_nim), use_nim=use_nim)
    complaints, recalls = await pipeline.ingest(input_data.vehicle(), refresh=input_data.refresh)
    target = next(
        (item for item in recalls if item.campaign_number == input_data.target_campaign_number),
        None,
    )
    if target is None:
        target = await pipeline.nhtsa.fetch_campaign(input_data.target_campaign_number)
    if target is None:
        return dumps_json(
            {
                "status": "TARGET_RECALL_NOT_FOUND",
                "campaign_number": input_data.target_campaign_number,
                "message": "Target recall text is required for post-hoc matching.",
            },
            indent=None,
        )
    result = await RecallTimeMachine(pipeline).run(
        vehicle=input_data.vehicle(),
        complaints=complaints,
        recalls=recalls,
        target_recall=target,
        official_recall_date=input_data.official_recall_date,
        replay_start_date=input_data.replay_start_date,
        alert_threshold=input_data.alert_threshold,
        minimum_evidence=input_data.minimum_evidence,
        use_signature_cache=not input_data.refresh,
    )
    payload = {
        "backtest_id": result.backtest_id,
        "status": result.status,
        "campaign": result.target_campaign_number,
        "official_recall_date": result.official_recall_date,
        "first_any_alert_date": result.first_any_alert_date,
        "first_matching_alert_date": result.first_matching_alert_date,
        "alert_snapshot_count": result.alert_snapshot_count,
        "lead_time_days": result.lead_time_days,
        "target_match_score": result.target_match_score,
        "max_pre_alert_target_score": result.max_pre_alert_target_score,
        "max_pre_alert_target_date": result.max_pre_alert_target_date,
        "max_pre_alert_signal_id": result.max_pre_alert_signal_id,
        "complaints_considered": result.complaints_considered,
        "anti_leakage_checks": result.anti_leakage_checks,
        "warnings": result.warnings,
        "alert_timeline": [
            {
                "cutoff_date": snapshot.cutoff_date,
                "visible_complaints": snapshot.complaint_count_visible,
                "max_risk_score": snapshot.max_risk_score,
                "distance_to_alert_threshold": snapshot.distance_to_alert_threshold,
                "top_candidates": [item.model_dump(mode="json") for item in snapshot.top_candidates],
                "alerts": [
                    {
                        "signal_id": signal.signal_id,
                        "issue": signal.cluster.label,
                        "risk_score": signal.risk.final_score,
                        "evidence_count": signal.cluster.evidence_count,
                    }
                    for signal in snapshot.alerts
                ],
            }
            for snapshot in result.snapshots
            if snapshot.alerts
        ],
    }
    return dumps_json(payload, indent=None)


async def execute_evidence(input_data: EvidenceToolInput, *, data_dir: str, use_nim: bool) -> str:
    pipeline = build_pipeline(settings_for_tool(data_dir, use_nim), use_nim=use_nim)
    complaint = pipeline.repository.find_complaint(input_data.odi_number)
    if complaint is None:
        return dumps_json({"status": "NOT_FOUND", "odi_number": input_data.odi_number}, indent=None)
    payload = complaint.model_dump(mode="json")
    payload["narrative_caveat"] = (
        "The complaint is an owner-reported record. Crash, fire, injury, and component fields do not by themselves prove causation."
    )
    return dumps_json(payload, indent=None)


async def execute_brief(input_data: BriefToolInput, *, data_dir: str, use_nim: bool) -> str:
    pipeline = build_pipeline(settings_for_tool(data_dir, use_nim), use_nim=use_nim)
    run = pipeline.repository.load_analysis_run(input_data.run_id)
    if run is None:
        return dumps_json({"status": "RUN_NOT_FOUND", "run_id": input_data.run_id}, indent=None)
    signal = next((item for item in run.signals if item.signal_id == input_data.signal_id), None)
    if signal is None:
        return dumps_json({"status": "SIGNAL_NOT_FOUND", "signal_id": input_data.signal_id}, indent=None)
    return EngineeringBriefRenderer().render_markdown(signal)
