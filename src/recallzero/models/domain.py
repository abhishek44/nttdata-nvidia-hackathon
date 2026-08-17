from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True, validate_assignment=True)


class ExtractionMethod(StrEnum):
    NIM = "nim"
    HEURISTIC = "heuristic"
    CACHED = "cached"


class EmbeddingMethod(StrEnum):
    NIM = "nim"
    TFIDF = "tfidf"


class SignalLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Vehicle(StrictModel):
    make: str = Field(min_length=1)
    model: str = Field(min_length=1)
    model_years: tuple[int, ...] = Field(min_length=1)

    @field_validator("make", "model")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return " ".join(value.strip().upper().split())

    @field_validator("model_years", mode="before")
    @classmethod
    def normalize_years(cls, value: Any) -> tuple[int, ...]:
        if isinstance(value, int):
            value = [value]
        years = sorted({int(item) for item in value})
        if not years:
            raise ValueError("At least one model year is required")
        for year in years:
            if year < 1900 or year > date.today().year + 2:
                raise ValueError(f"Unsupported model year: {year}")
        return tuple(years)

    @property
    def display_name(self) -> str:
        years = ", ".join(str(year) for year in self.model_years)
        return f"{years} {self.make} {self.model}"

    @property
    def slug(self) -> str:
        text = f"{'-'.join(str(y) for y in self.model_years)}-{self.make}-{self.model}"
        return "-".join("".join(ch.lower() if ch.isalnum() else " " for ch in text).split())


class Complaint(StrictModel):
    odi_number: str = Field(min_length=1)
    vehicle: Vehicle
    manufacturer: str | None = None
    received_date: date
    incident_date: date | None = None
    components: tuple[str, ...] = Field(default_factory=tuple)
    narrative: str = Field(min_length=1)
    crash: bool = False
    fire: bool = False
    injuries: int = Field(default=0, ge=0)
    deaths: int = Field(default=0, ge=0)
    vin_prefix: str | None = None
    mileage: int | None = Field(default=None, ge=0)
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("odi_number", mode="before")
    @classmethod
    def normalize_odi(cls, value: Any) -> str:
        if value is None:
            raise ValueError("Complaint ODI number is required")
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("Complaint ODI number is required")
        return normalized

    @field_validator("components", mode="before")
    @classmethod
    def normalize_components(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return tuple()
        if isinstance(value, str):
            parts = value.replace(";", ",").split(",")
        else:
            parts = list(value)
        return tuple(sorted({" ".join(str(part).strip().upper().split()) for part in parts if str(part).strip()}))

    @field_validator("manufacturer")
    @classmethod
    def normalize_manufacturer(cls, value: str | None) -> str | None:
        return " ".join(value.strip().split()) if value else None


class Recall(StrictModel):
    campaign_number: str = Field(min_length=1)
    vehicle: Vehicle | None = None
    manufacturer: str | None = None
    report_received_date: date | None = None
    component: str | None = None
    summary: str | None = None
    consequence: str | None = None
    remedy: str | None = None
    notes: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("campaign_number")
    @classmethod
    def normalize_campaign(cls, value: str) -> str:
        return value.strip().upper().replace("-", "")


class FailureSignature(StrictModel):
    complaint_id: str
    system: str
    subsystem: str | None = None
    failure_mode: str
    symptom: str | None = None
    operating_state: str | None = None
    consequence: str | None = None
    severity_indicators: tuple[str, ...] = Field(default_factory=tuple)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    extraction_method: ExtractionMethod = ExtractionMethod.HEURISTIC
    model_name: str | None = None

    @field_validator("system", "subsystem", "failure_mode", "symptom", "operating_state", "consequence")
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None

    @field_validator("severity_indicators", mode="before")
    @classmethod
    def normalize_indicators(cls, value: Any) -> tuple[str, ...]:
        if not value:
            return tuple()
        return tuple(sorted({str(item).strip().lower().replace(" ", "_") for item in value if str(item).strip()}))

    @model_validator(mode="after")
    def require_core_fields(self) -> FailureSignature:
        if not self.system:
            self.system = "UNKNOWN"
        if not self.failure_mode:
            self.failure_mode = "UNSPECIFIED FAILURE"
        return self

    def canonical_text(self) -> str:
        parts = [
            f"system: {self.system}",
            f"subsystem: {self.subsystem or 'unknown'}",
            f"failure mode: {self.failure_mode}",
            f"symptom: {self.symptom or 'unknown'}",
            f"operating state: {self.operating_state or 'unknown'}",
            f"consequence: {self.consequence or 'unknown'}",
            f"severity indicators: {', '.join(self.severity_indicators) or 'none'}",
        ]
        return "\n".join(parts)


class ClusterMember(StrictModel):
    complaint_id: str
    similarity_to_representative: float | None = Field(default=None, ge=-1.0, le=1.0)


class ComplaintCluster(StrictModel):
    cluster_id: str
    label: str
    system: str
    failure_mode: str
    member_ids: tuple[str, ...]
    members: tuple[ClusterMember, ...] = Field(default_factory=tuple)
    representative_complaint_ids: tuple[str, ...] = Field(default_factory=tuple)
    first_received_date: date
    last_received_date: date
    is_noise: bool = False
    embedding_method: EmbeddingMethod

    @property
    def evidence_count(self) -> int:
        return len(self.member_ids)


class TrendMetrics(StrictModel):
    cutoff_date: date
    recent_window_days: int
    baseline_window_days: int
    recent_count: int = Field(ge=0)
    baseline_count: int = Field(ge=0)
    recent_rate_per_28d: float = Field(ge=0)
    baseline_rate_per_28d: float = Field(ge=0)
    trend_ratio: float = Field(ge=0)
    acceleration_score: float = Field(ge=0, le=100)
    persistence_weeks: int = Field(ge=0)
    persistence_score: float = Field(ge=0, le=100)
    evidence_score: float = Field(ge=0, le=100)


class RecallMatch(StrictModel):
    matched: bool
    campaign_number: str | None = None
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str
    recall_date: date | None = None


class RiskFactor(StrictModel):
    name: str
    score: float = Field(ge=0, le=100)
    weight: float = Field(ge=0, le=1)
    contribution: float = Field(ge=0, le=100)
    explanation: str


class RiskAssessment(StrictModel):
    final_score: float = Field(ge=0, le=100)
    level: SignalLevel
    factors: tuple[RiskFactor, ...]
    threshold: float = Field(ge=0, le=100)
    minimum_evidence: int = Field(ge=1)
    alert: bool
    rationale: str


class EvidenceItem(StrictModel):
    complaint_id: str
    received_date: date
    components: tuple[str, ...]
    narrative_excerpt: str
    crash: bool
    fire: bool
    injuries: int
    deaths: int
    signature: FailureSignature


class DefectSignal(StrictModel):
    signal_id: str
    vehicle: Vehicle
    cutoff_date: date
    cluster: ComplaintCluster
    trend: TrendMetrics
    recall_match: RecallMatch
    risk: RiskAssessment
    evidence: tuple[EvidenceItem, ...]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AnalysisRun(StrictModel):
    run_id: str
    vehicle: Vehicle
    cutoff_date: date
    complaint_count: int
    recall_count_visible: int
    signature_count: int
    cluster_count: int
    signals: tuple[DefectSignal, ...]
    extraction_method_counts: dict[str, int]
    embedding_method: EmbeddingMethod
    warnings: tuple[str, ...] = Field(default_factory=tuple)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BacktestSnapshot(StrictModel):
    cutoff_date: date
    complaint_count_visible: int
    signal_count: int
    alerts: tuple[DefectSignal, ...]


class BacktestResult(StrictModel):
    backtest_id: str
    vehicle: Vehicle
    target_campaign_number: str
    official_recall_date: date
    first_matching_alert_date: date | None
    lead_time_days: int | None
    matched_signal_id: str | None
    target_match_score: float | None
    status: str
    snapshots: tuple[BacktestSnapshot, ...]
    complaints_considered: int
    latest_complaint_date_used: date | None
    anti_leakage_checks: dict[str, bool]
    warnings: tuple[str, ...] = Field(default_factory=tuple)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
