from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException

from recallzero.backtest.time_machine import run_time_machine
from recallzero.clients.nhtsa import NHTSAClient
from recallzero.models import Complaint, EnrichedComplaint, FailureSignature, VehicleKey

app = FastAPI(title="RecallZero API", version="0.1.0")
client = NHTSAClient()
CANDIDATES_DIR = Path(__file__).resolve().parents[2] / "data" / "candidates"


@app.get("/health")
def health():
    return {"status": "ok"}


def _safe(label: str, fn):
    try:
        return {"ok": True, "data": fn(), "error": None}
    except Exception as exc:
        return {"ok": False, "data": None, "error": f"{label} unavailable: {exc}"}


def _top_components(complaints: list[Complaint]) -> str:
    counts: dict[str, int] = {}
    for complaint in complaints:
        for component in complaint.components:
            counts[component] = counts.get(component, 0) + 1
    if not counts:
        return "No live component data yet"
    return ", ".join(f"{name} ({count})" for name, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:3])


def _candidate_rows() -> list[dict]:
    rows = []
    for path in sorted(CANDIDATES_DIR.glob("*.json")):
        cfg = json.loads(path.read_text(encoding="utf-8"))
        rows.append(
            {
                "campaign_number": cfg.get("campaign_number", path.stem),
                "manufacturer": cfg.get("manufacturer"),
                "recall_date": cfg.get("recall_date"),
                "problem": cfg.get("problem"),
                "vehicles": cfg.get("vehicles", []),
                "status": cfg.get("status"),
                "source_note": cfg.get("source_note"),
                "target_terms": cfg.get("target_terms", []),
            }
        )
    return rows


def _demo_backtest_payload() -> dict:
    vehicle = VehicleKey(make="DEMO", model="EV", model_year=2022)
    start = date(2022, 1, 1)
    dates = [0, 10, 25, 48, 55, 60, 66, 72, 78, 84, 90, 96]
    rows = []
    for i, offset in enumerate(dates, 1):
        complaint = Complaint(
            odi_number=f"SYN-{i:03d}",
            vehicle=vehicle,
            date_complaint_filed=start + timedelta(days=offset),
            components=["ELECTRICAL SYSTEM", "POWER TRAIN"],
            summary="Vehicle lost motive power while driving and displayed Stop Safely Now.",
        )
        rows.append(
            EnrichedComplaint(
                complaint=complaint,
                signature=FailureSignature(
                    system="ELECTRICAL SYSTEM",
                    subsystem="HIGH_VOLTAGE_BATTERY",
                    failure_mode="LOSS_OF_MOTIVE_POWER",
                    operating_state="VEHICLE_MOVING",
                    consequence="LOSS OF MOTIVE POWER",
                    severity_indicators=["LOSS_OF_MOTIVE_POWER"],
                    confidence=0.9,
                ),
                cluster_id="cluster-000",
            )
        )
    return run_time_machine(rows, "DEMO-RECALL", date(2022, 5, 15), alert_threshold=70, min_reports=4).model_dump(mode="json")


def _fallback_signal(candidates_data: list[dict]) -> dict:
    candidate = candidates_data[0] if candidates_data else {}
    vehicles = candidate.get("vehicles") or []
    vehicle_text = ", ".join(f"{v.get('model_year')} {v.get('make')} {v.get('model')}" for v in vehicles)
    return {
        "status": "CANDIDATE",
        "title": f"{candidate.get('campaign_number', 'Candidate')}: Candidate safety signal",
        "subtitle": vehicle_text or "Backend candidate vehicle",
        "risk_score": "N/A",
        "metrics": [
            {"label": "Complaints", "value": "N/A"},
            {"label": "First alert", "value": "N/A"},
            {"label": "Source", "value": "Candidate file"},
        ],
        "note": candidate.get("problem") or "Backend candidate data is unavailable.",
        "tone": "green",
    }


def _signal_tone(signature: FailureSignature) -> str:
    text = " ".join(
        [
            signature.system or "",
            signature.subsystem or "",
            signature.failure_mode or "",
            signature.consequence or "",
            " ".join(signature.severity_indicators),
        ]
    ).upper()
    if "FIRE" in text or "THERMAL" in text or "OVERHEAT" in text:
        return "red"
    if "BRAK" in text:
        return "orange"
    if "STEER" in text or "SUSPENSION" in text:
        return "blue"
    if "POWER TRAIN" in text or "MOTIVE_POWER" in text or "ENGINE" in text:
        return "purple"
    if "ELECTRICAL" in text or "BATTERY" in text or "VOLTAGE" in text:
        return "green"
    return "slate"


@app.get("/dashboard")
def dashboard(year: int = 2021, make: str = "Ford", model: str = "Mustang Mach-E"):
    vehicle = VehicleKey(make=make, model=model, model_year=year)
    complaints_result = _safe("complaints", lambda: client.complaints_by_vehicle(vehicle))
    recalls_result = _safe("recalls", lambda: client.recalls_by_vehicle(vehicle))
    candidates_result = _safe("candidates", _candidate_rows)

    complaints_data: list[Complaint] = complaints_result["data"] or []
    recalls_data = recalls_result["data"] or []
    candidates_data = candidates_result["data"] or []

    def load_signals():
        from recallzero.pipeline import enrich_and_cluster

        return enrich_and_cluster(complaints_data)

    signals_result = _safe("signals", load_signals) if complaints_result["ok"] else {"ok": False, "data": [], "error": "signals unavailable: complaints endpoint has no live rows"}
    signals_data: list[EnrichedComplaint] = signals_result["data"] or []
    clusters: dict[str, list[EnrichedComplaint]] = {}
    for item in signals_data:
        clusters.setdefault(item.cluster_id or "unclustered", []).append(item)

    signal_cards = []
    for index, (cluster_id, items) in enumerate(sorted(clusters.items(), key=lambda item: len(item[1]), reverse=True)[:4]):
        signature = items[0].signature
        signal_cards.append(
            {
                "status": signature.system,
                "title": signature.failure_mode.replace("_", " ").title(),
                "subtitle": f"{signature.subsystem or 'General'} · {signature.consequence or 'Evidence cluster from backend'}",
                "risk_score": min(96, 52 + len(items) * 5),
                "metrics": [
                    {"label": "Complaints", "value": len(items)},
                    {"label": "State", "value": signature.operating_state or "Unknown"},
                    {"label": "Cluster", "value": cluster_id},
                ],
                "note": "",
                "selected": index == 0,
                "tone": _signal_tone(signature),
            }
        )
    if not signal_cards:
        signal_cards = [_fallback_signal(candidates_data)]

    candidate = candidates_data[0] if candidates_data else {}
    first_signal = signals_data[0].signature if signals_data else None
    issue_count = sum(1 for result in [complaints_result, recalls_result, candidates_result, signals_result] if result.get("error"))
    timeline: list[dict] = []
    first_timeline = timeline[0] if timeline else {}
    last_timeline = timeline[-1] if timeline else {}
    alert_points = [point for point in timeline if point.get("alert")]

    return {
        "ui": {
            "brand": {"prefix": "RECALL", "accent": "ZERO"},
            "nav": [
                {"label": "Safety Radar", "icon": "radar", "active": True},
                {"label": "Investigations", "icon": "search", "active": False},
                {"label": "Time Machine", "icon": "clock", "active": False},
                {"label": "Recalls", "icon": "recall", "active": False},
                {"label": "Data Explorer", "icon": "data", "active": False},
            ],
            "filters": ["30D", "90D", "6M", "1Y", "Filters"],
            "title": "SAFETY RADAR",
            "live_label": "LIVE SAFETY WATCH",
            "subtitle": f"Backend-connected view for {year} {make} {model}",
        },
        "vehicle": {"year": year, "make": make, "model": model},
        "status": {
            "fastapi": "Connected",
            "detail": "All dashboard content is supplied by FastAPI.",
            "nhtsa_data": "Live" if complaints_data else "Pending",
            "signal_engine": "Live" if clusters else "Fallback",
            "issue_label": f"Limited: {issue_count} backend source(s) pending" if issue_count else "Operational",
        },
        "kpis": [
            {"key": "signals", "label": "Signal clusters", "value": len(clusters), "hint": "from backend /signals", "tone": "red", "icon": "warning"},
            {"key": "complaints", "label": "Complaints analyzed", "value": len(complaints_data), "hint": "from backend /complaints", "tone": "orange", "icon": "stack"},
            {"key": "recalls", "label": "Recall records", "value": len(recalls_data), "hint": "from backend /recalls", "tone": "blue", "icon": "data"},
            {"key": "lead", "label": "Avg. early warning", "value": "N/A", "hint": "requires backtest data", "tone": "green", "icon": "trend"},
        ],
        "top_components": _top_components(complaints_data),
        "signals": signal_cards,
        "signature": {
            "system": first_signal.system if first_signal else "Candidate",
            "subsystem": first_signal.subsystem if first_signal else candidate.get("campaign_number", "Candidate"),
            "failure_mode": first_signal.failure_mode if first_signal else "Loss of motive power",
            "operating_state": first_signal.operating_state if first_signal else "Vehicle moving",
            "consequence": first_signal.consequence if first_signal else "Safety risk",
            "confidence": first_signal.confidence if first_signal else "N/A",
        },
        "trend": {
            "title": "COMPLAINT TREND",
            "label": "backend data",
            "velocity_label": "Risk velocity",
            "velocity_hint": "No backtest data",
            "score": "N/A",
            "timeline": timeline,
            "summary": "No backend trend timeline is available for this vehicle.",
            "x_start": first_timeline.get("date", "Start"),
            "x_end": last_timeline.get("date", "End"),
            "y_label": "Risk score",
            "threshold": 70,
            "alert_date": alert_points[0].get("date") if alert_points else None,
        },
        "complaints": [
            {
                "index": index,
                "odi": complaint.odi_number,
                "date": complaint.date_complaint_filed.isoformat(),
                "components": complaint.components,
                "summary": complaint.summary,
                "evidence": complaint.components[:2],
            }
            for index, complaint in enumerate(complaints_data[:6], 1)
        ],
        "complaints_empty": "No live complaint rows returned yet. Start the backend and verify NHTSA access.",
        "details": {
            "badge": candidate.get("campaign_number", "Candidate"),
            "title": candidate.get("problem", "Backend candidate problem is unavailable."),
            "subtitle": ", ".join(f"{v.get('model_year')} {v.get('make')} {v.get('model')}" for v in candidate.get("vehicles", [])),
            "risk_score": "N/A",
            "first_alert": "N/A",
            "matching_recall": len(recalls_data),
            "copy": "Evidence is loaded from FastAPI endpoints.",
        },
        "time_machine": {
            "known_recall": candidate.get("recall_date") or "N/A",
            "alert_threshold": "N/A",
            "replay_steps": "N/A",
            "lead_time_days": "N/A",
            "lead_label": "days early",
        },
        "recalls": [
            {
                "campaign": recall.campaign_number,
                "date": recall.report_received_date.isoformat(),
                "component": recall.component,
                "summary": recall.summary,
            }
            for recall in recalls_data[:4]
        ],
        "recalls_empty": "No recall rows returned for this vehicle.",
    }


@app.get("/vehicles/{year}/{make}/{model}/complaints")
def vehicle_complaints(year: int, make: str, model: str):
    try:
        complaints = client.complaints_by_vehicle(VehicleKey(make=make, model=model, model_year=year))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"NHTSA request failed: {exc}") from exc
    return {"count": len(complaints), "results": [c.model_dump(mode="json") for c in complaints]}


@app.get("/vehicles/{year}/{make}/{model}/recalls")
def vehicle_recalls(year: int, make: str, model: str):
    try:
        recalls = client.recalls_by_vehicle(VehicleKey(make=make, model=model, model_year=year))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"NHTSA request failed: {exc}") from exc
    return {"count": len(recalls), "results": [r.model_dump(mode="json") for r in recalls]}


@app.get("/vehicles/{year}/{make}/{model}/signals")
def vehicle_signals(year: int, make: str, model: str):
    try:
        from recallzero.pipeline import enrich_and_cluster

        complaints = client.complaints_by_vehicle(VehicleKey(make=make, model=model, model_year=year))
        enriched = enrich_and_cluster(complaints)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    by_cluster: dict[str, list] = {}
    for item in enriched:
        by_cluster.setdefault(item.cluster_id or "unclustered", []).append(item)
    return {
        "vehicle": {"year": year, "make": make, "model": model},
        "clusters": [
            {
                "cluster_id": cid,
                "count": len(rows),
                "signature": rows[0].signature.model_dump(),
                "complaint_ids": [r.complaint.odi_number for r in rows],
            }
            for cid, rows in sorted(by_cluster.items(), key=lambda kv: len(kv[1]), reverse=True)
        ],
    }


@app.get("/candidates")
def candidates():
    rows = []
    for path in sorted(CANDIDATES_DIR.glob("*.json")):
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail=f"Invalid candidate file {path.name}: {exc}") from exc
        rows.append(
            {
                "campaign_number": cfg.get("campaign_number", path.stem),
                "manufacturer": cfg.get("manufacturer"),
                "recall_date": cfg.get("recall_date"),
                "problem": cfg.get("problem"),
                "vehicles": cfg.get("vehicles", []),
                "status": cfg.get("status"),
                "source_note": cfg.get("source_note"),
                "target_terms": cfg.get("target_terms", []),
            }
        )
    return {"count": len(rows), "results": rows}


@app.get("/candidates/{campaign_number}/backtest")
def candidate_backtest(campaign_number: str):
    path = CANDIDATES_DIR / f"{campaign_number}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Candidate {campaign_number} was not found")
    try:
        from recallzero.backtest.candidate_runner import run_candidate

        return run_candidate(path)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Candidate backtest failed: {exc}") from exc


@app.get("/backtests/demo")
def demo_backtest():
    vehicle = VehicleKey(make="DEMO", model="EV", model_year=2022)
    start = date(2022, 1, 1)
    dates = [0, 10, 25, 48, 55, 60, 66, 72, 78, 84, 90, 96]
    rows = []
    for i, offset in enumerate(dates, 1):
        complaint = Complaint(
            odi_number=f"SYN-{i:03d}",
            vehicle=vehicle,
            date_complaint_filed=start + timedelta(days=offset),
            components=["ELECTRICAL SYSTEM", "POWER TRAIN"],
            summary="Vehicle lost motive power while driving and displayed Stop Safely Now.",
        )
        rows.append(
            EnrichedComplaint(
                complaint=complaint,
                signature=FailureSignature(
                    system="ELECTRICAL SYSTEM",
                    subsystem="HIGH_VOLTAGE_BATTERY",
                    failure_mode="LOSS_OF_MOTIVE_POWER",
                    operating_state="VEHICLE_MOVING",
                    consequence="LOSS OF MOTIVE POWER",
                    severity_indicators=["LOSS_OF_MOTIVE_POWER"],
                    confidence=0.9,
                ),
                cluster_id="cluster-000",
            )
        )
    result = run_time_machine(rows, "DEMO-RECALL", date(2022, 5, 15), alert_threshold=70, min_reports=4)
    return result.model_dump(mode="json")
