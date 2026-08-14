from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

import httpx

from recallzero.config import settings
from recallzero.models import Complaint, Recall, VehicleKey


def _parse_date(value: str | None):
    if not value:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value[:10], fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported NHTSA date: {value}")


class NHTSAClient:
    """Thin client around the public NHTSA complaints and recalls APIs."""

    def __init__(self, base_url: str | None = None, timeout: float = 30.0):
        self.base_url = (base_url or settings.nhtsa_base_url).rstrip("/")
        self.timeout = timeout

    def _get(self, path: str) -> dict:
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            response = client.get(f"{self.base_url}{path}")
            response.raise_for_status()
            return response.json()

    def complaints_by_vehicle(self, vehicle: VehicleKey) -> list[Complaint]:
        path = (
            "/complaints/complaintsByVehicle"
            f"?make={quote(vehicle.make)}&model={quote(vehicle.model)}&modelYear={vehicle.model_year}"
        )
        payload = self._get(path)
        results = payload.get("results", [])
        complaints: list[Complaint] = []
        for row in results:
            filed = _parse_date(row.get("dateComplaintFiled"))
            if not filed:
                continue
            components = [p.strip() for p in (row.get("components") or "").split(",") if p.strip()]
            complaints.append(
                Complaint(
                    odi_number=row.get("odiNumber", "unknown"),
                    vehicle=vehicle,
                    manufacturer=row.get("manufacturer"),
                    date_of_incident=_parse_date(row.get("dateOfIncident")),
                    date_complaint_filed=filed,
                    components=components,
                    summary=row.get("summary") or "",
                    crash=bool(row.get("crash")),
                    fire=bool(row.get("fire")),
                    injuries=int(row.get("numberOfInjuries") or 0),
                    deaths=int(row.get("numberOfDeaths") or 0),
                    vin_prefix=row.get("vin"),
                    raw=row,
                )
            )
        return complaints

    def recalls_by_vehicle(self, vehicle: VehicleKey) -> list[Recall]:
        path = (
            "/recalls/recallsByVehicle"
            f"?make={quote(vehicle.make)}&model={quote(vehicle.model)}&modelYear={vehicle.model_year}"
        )
        payload = self._get(path)
        results = payload.get("results", [])
        recalls: list[Recall] = []
        for row in results:
            report_date = _parse_date(row.get("ReportReceivedDate"))
            if not report_date:
                continue
            recalls.append(
                Recall(
                    campaign_number=row.get("NHTSACampaignNumber", "unknown"),
                    report_received_date=report_date,
                    manufacturer=row.get("Manufacturer") or "",
                    component=row.get("Component") or "",
                    summary=row.get("Summary") or "",
                    consequence=row.get("Consequence"),
                    remedy=row.get("Remedy"),
                    raw=row,
                )
            )
        return recalls

    def recall_by_campaign(self, campaign_number: str) -> list[Recall]:
        path = f"/recalls/campaignNumber?campaignNumber={quote(campaign_number)}"
        payload = self._get(path)
        results = payload.get("results", [])
        recalls: list[Recall] = []
        for row in results:
            report_date = _parse_date(row.get("ReportReceivedDate"))
            if not report_date:
                continue
            recalls.append(
                Recall(
                    campaign_number=row.get("NHTSACampaignNumber", campaign_number),
                    report_received_date=report_date,
                    manufacturer=row.get("Manufacturer") or "",
                    component=row.get("Component") or "",
                    summary=row.get("Summary") or "",
                    consequence=row.get("Consequence"),
                    remedy=row.get("Remedy"),
                    raw=row,
                )
            )
        return recalls
