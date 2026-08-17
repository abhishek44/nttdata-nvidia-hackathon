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
    return {
        "run_id": run.run_id,
        "vehicle": run.vehicle.model_dump(mode="json"),
        "cutoff_date": run.cutoff_date.isoformat(),
        "complaint_count": run.complaint_count,
        "recall_count_visible": run.recall_count_visible,
        "cluster_count": run.cluster_count,
        "extraction_methods": run.extraction_method_counts,
        "embedding_method": run.embedding_method.value,
        "warnings": list(run.warnings),
        "signals": [
            {
                "signal_id": signal.signal_id,
                "issue": signal.cluster.label,
                "alert": signal.risk.alert,
                "risk_level": signal.risk.level.value,
                "risk_score": signal.risk.final_score,
                "evidence_count": signal.cluster.evidence_count,
                "recent_count": signal.trend.recent_count,
                "baseline_count": signal.trend.baseline_count,
                "trend_ratio": signal.trend.trend_ratio,
                "persistence_weeks": signal.trend.persistence_weeks,
                "recall_match": signal.recall_match.model_dump(mode="json"),
                "rationale": signal.risk.rationale,
                "evidence_ids": [item.complaint_id for item in signal.evidence],
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
        "first_matching_alert_date": result.first_matching_alert_date,
        "lead_time_days": result.lead_time_days,
        "target_match_score": result.target_match_score,
        "complaints_considered": result.complaints_considered,
        "anti_leakage_checks": result.anti_leakage_checks,
        "warnings": result.warnings,
        "alert_timeline": [
            {
                "cutoff_date": snapshot.cutoff_date,
                "visible_complaints": snapshot.complaint_count_visible,
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
