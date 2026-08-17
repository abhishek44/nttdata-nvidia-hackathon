from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from recallzero.models.domain import Vehicle


class VehicleRequest(BaseModel):
    make: str
    model: str
    model_years: list[int] = Field(min_length=1)

    def to_vehicle(self) -> Vehicle:
        return Vehicle(make=self.make, model=self.model, model_years=tuple(self.model_years))


class IngestRequest(VehicleRequest):
    refresh: bool = False


class AnalyzeRequest(VehicleRequest):
    cutoff_date: date | None = None
    refresh: bool = False
    use_nim: bool | None = None
    max_complaints: int | None = Field(default=None, ge=1, le=10000)


class BacktestRequest(VehicleRequest):
    target_campaign_number: str
    official_recall_date: date
    replay_start_date: date | None = None
    refresh: bool = False
    use_nim: bool | None = None
    alert_threshold: float | None = Field(default=None, ge=0, le=100)
    minimum_evidence: int | None = Field(default=None, ge=1)
