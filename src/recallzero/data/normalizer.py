from __future__ import annotations

from datetime import date, datetime
from typing import Any

from dateutil import parser as date_parser

from recallzero.models import Complaint, Recall, Vehicle


def parse_date(value: Any, *, day_first: bool = False) -> date | None:
    if value in (None, "", 0):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    slash_formats = ("%d/%m/%Y", "%m/%d/%Y") if day_first else ("%m/%d/%Y", "%d/%m/%Y")
    for fmt in (*slash_formats, "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    try:
        return date_parser.parse(text, fuzzy=False, dayfirst=day_first).date()
    except (ValueError, TypeError, OverflowError):
        return None


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (ValueError, TypeError):
        return default


def normalize_complaint(payload: dict[str, Any], requested_vehicle: Vehicle) -> Complaint:
    products = payload.get("products") or []
    product = products[0] if products else {}
    year = _to_int(product.get("productYear"), requested_vehicle.model_years[0])
    vehicle = Vehicle(
        make=product.get("productMake") or requested_vehicle.make,
        model=product.get("productModel") or requested_vehicle.model,
        model_years=(year,),
    )
    received = parse_date(payload.get("dateComplaintFiled") or payload.get("dateReceived") or payload.get("ldate"))
    if received is None:
        raise ValueError(f"Complaint {payload.get('odiNumber')} has no valid received date")
    narrative = str(payload.get("summary") or payload.get("description") or payload.get("cdescr") or "").strip()
    if not narrative:
        raise ValueError(f"Complaint {payload.get('odiNumber')} has no narrative")

    vin = str(payload.get("vin") or "").strip() or None
    return Complaint(
        odi_number=payload.get("odiNumber") or payload.get("odi_number") or payload.get("cmplid"),
        vehicle=vehicle,
        manufacturer=payload.get("manufacturer"),
        received_date=received,
        incident_date=parse_date(payload.get("dateOfIncident") or payload.get("faildate")),
        components=payload.get("components") or payload.get("component") or payload.get("compdesc"),
        narrative=narrative,
        crash=_to_bool(payload.get("crash")),
        fire=_to_bool(payload.get("fire")),
        injuries=_to_int(payload.get("numberOfInjuries") or payload.get("injured")),
        deaths=_to_int(payload.get("numberOfDeaths") or payload.get("deaths")),
        vin_prefix=vin[:11] if vin else None,
        mileage=_to_int(payload.get("mileage") or payload.get("miles"), default=0) or None,
        raw_payload=payload,
    )


def normalize_recall(payload: dict[str, Any], requested_vehicle: Vehicle | None = None) -> Recall:
    campaign = payload.get("NHTSACampaignNumber") or payload.get("nhtsaCampaignNumber") or payload.get("campaignNumber")
    if not campaign:
        raise ValueError("Recall payload has no campaign number")
    vehicle = None
    year_value = payload.get("ModelYear") or payload.get("modelYear")
    if requested_vehicle is not None:
        year = _to_int(year_value, requested_vehicle.model_years[0])
        vehicle = Vehicle(
            make=payload.get("Make") or payload.get("make") or requested_vehicle.make,
            model=payload.get("Model") or payload.get("model") or requested_vehicle.model,
            model_years=(year,),
        )
    return Recall(
        campaign_number=str(campaign),
        vehicle=vehicle,
        manufacturer=payload.get("Manufacturer") or payload.get("manufacturer"),
        report_received_date=parse_date(
            payload.get("ReportReceivedDate") or payload.get("reportReceivedDate"),
            day_first=True,
        ),
        component=payload.get("Component") or payload.get("component"),
        summary=payload.get("Summary") or payload.get("summary"),
        consequence=payload.get("Consequence") or payload.get("consequence"),
        remedy=payload.get("Remedy") or payload.get("remedy"),
        notes=payload.get("Notes") or payload.get("notes"),
        raw_payload=payload,
    )
