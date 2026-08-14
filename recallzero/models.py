from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class VehicleKey(BaseModel):
    make: str
    model: str
    model_year: int


class Complaint(BaseModel):
    odi_number: int | str
    vehicle: VehicleKey
    manufacturer: str | None = None
    date_of_incident: date | None = None
    date_complaint_filed: date
    components: list[str] = Field(default_factory=list)
    summary: str
    crash: bool = False
    fire: bool = False
    injuries: int = 0
    deaths: int = 0
    vin_prefix: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class FailureSignature(BaseModel):
    system: str
    subsystem: str | None = None
    failure_mode: str
    operating_state: str | None = None
    consequence: str | None = None
    symptoms: list[str] = Field(default_factory=list)
    severity_indicators: list[str] = Field(default_factory=list)
    confidence: float = 0.5

    def canonical_text(self) -> str:
        parts = [self.system, self.subsystem or "", self.failure_mode, self.operating_state or "", self.consequence or ""]
        return " | ".join(p.strip() for p in parts if p and p.strip())


class EnrichedComplaint(BaseModel):
    complaint: Complaint
    signature: FailureSignature
    cluster_id: str | None = None


class Recall(BaseModel):
    campaign_number: str
    report_received_date: date
    manufacturer: str
    component: str
    summary: str
    consequence: str | None = None
    remedy: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class TrendMetrics(BaseModel):
    cutoff_date: date
    total_reports: int
    recent_reports: int
    baseline_reports: int
    recent_weekly_rate: float
    baseline_weekly_rate: float
    acceleration: float
    persistence_weeks: int


class RiskBreakdown(BaseModel):
    severity: float
    trend: float
    persistence: float
    evidence: float
    recall_gap: float
    final_score: float
    reasons: list[str]


class BacktestResult(BaseModel):
    campaign_number: str
    recall_date: date
    first_alert_date: date | None
    lead_time_days: int | None
    max_score: float
    alert_threshold: float
    timeline: list[dict[str, Any]]
