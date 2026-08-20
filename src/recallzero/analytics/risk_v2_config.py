from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator


class RiskWeightsV2(BaseModel):
    """Detector v2 six-factor risk weights."""

    volume_increase: float = 0.25
    vehicle_coverage: float = 0.20
    safety_consequence: float = 0.20
    persistence: float = 0.15
    model_year_breadth: float = 0.10
    no_matching_recall: float = 0.10

    @model_validator(mode="after")
    def validate_sum(self) -> "RiskWeightsV2":
        total = (
            self.volume_increase
            + self.vehicle_coverage
            + self.safety_consequence
            + self.persistence
            + self.model_year_breadth
            + self.no_matching_recall
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Risk weights must sum to 1.0, got {total}")
        return self


class RiskConfigV2(BaseModel):
    weights: RiskWeightsV2 = Field(default_factory=RiskWeightsV2)
    alert_threshold: float = Field(default=40.0, ge=0, le=100)
    minimum_evidence: int = Field(default=4, ge=1)
    medium_threshold: float = Field(default=40.0, ge=0, le=100)
    high_threshold: float = Field(default=70.0, ge=0, le=100)
    critical_threshold: float = Field(default=85.0, ge=0, le=100)


class TrendConfigV2(BaseModel):
    recent_window_days: int = Field(default=30, ge=7)
    baseline_window_days: int = Field(default=90, ge=28)
    persistence_weeks_to_full_score: int = Field(default=4, ge=1)


class DetectorV2Config(BaseModel):
    risk: RiskConfigV2 = Field(default_factory=RiskConfigV2)
    trend: TrendConfigV2 = Field(default_factory=TrendConfigV2)

    @classmethod
    def from_yaml(cls, path: Path) -> "DetectorV2Config":
        if not path.exists():
            return cls()
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        return cls.model_validate(raw.get("detector_v2", raw))