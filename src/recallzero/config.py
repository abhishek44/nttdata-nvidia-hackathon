from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RiskWeights(BaseModel):
    severity: float = 0.30
    trend: float = 0.25
    persistence: float = 0.15
    evidence: float = 0.20
    recall_gap: float = 0.10

    @model_validator(mode="after")
    def validate_sum(self) -> "RiskWeights":
        total = self.severity + self.trend + self.persistence + self.evidence + self.recall_gap
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Risk weights must sum to 1.0, got {total}")
        return self


class RiskConfig(BaseModel):
    weights: RiskWeights = Field(default_factory=RiskWeights)
    alert_threshold: float = Field(default=75.0, ge=0, le=100)
    minimum_evidence: int = Field(default=4, ge=1)
    medium_threshold: float = Field(default=45.0, ge=0, le=100)
    high_threshold: float = Field(default=70.0, ge=0, le=100)
    critical_threshold: float = Field(default=88.0, ge=0, le=100)

    @classmethod
    def from_yaml(cls, path: Path) -> "RiskConfig":
        if not path.exists():
            return cls()
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        return cls.model_validate(raw.get("risk", raw))


class ClusteringConfig(BaseModel):
    eps: float = Field(default=0.34, gt=0, lt=1)
    min_samples: int = Field(default=2, ge=1)
    max_representatives: int = Field(default=3, ge=1, le=10)


class TrendConfig(BaseModel):
    recent_window_days: int = Field(default=28, ge=7)
    baseline_window_days: int = Field(default=84, ge=28)
    persistence_weeks_to_full_score: int = Field(default=4, ge=1)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RECALLZERO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    data_dir: Path = Path("data")
    config_dir: Path = Path("config")
    nhtsa_base_url: str = "https://api.nhtsa.gov"
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    embedding_base_url: str | None = None
    llm_model: str = "nvidia/nemotron-3.5-lightning-30b-a3b"
    embedding_model: str = "nvidia/nv-embedqa-e5-v5"
    use_nim: bool = True
    llm_concurrency: int = Field(default=4, ge=1, le=32)
    request_timeout_seconds: float = Field(default=90, ge=5, le=600)
    max_retries: int = Field(default=3, ge=0, le=10)
    log_level: str = "INFO"
    nvidia_api_key: str | None = Field(default=None, validation_alias="NVIDIA_API_KEY")
    risk_file: Path = Path("config/risk.yml")
    clustering: ClusteringConfig = Field(default_factory=ClusteringConfig)
    trend: TrendConfig = Field(default_factory=TrendConfig)

    @field_validator("data_dir", "config_dir", "risk_file", mode="before")
    @classmethod
    def coerce_path(cls, value: Any) -> Path:
        return Path(value)

    def ensure_directories(self) -> None:
        for name in ("raw", "normalized", "cache", "runs"):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)

    def risk_config(self) -> RiskConfig:
        return RiskConfig.from_yaml(self.risk_file)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings


def reset_settings_cache() -> None:
    get_settings.cache_clear()
