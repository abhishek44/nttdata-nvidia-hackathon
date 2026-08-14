from __future__ import annotations

from fastapi import FastAPI, HTTPException

from recallzero.clients.nhtsa import NHTSAClient
from recallzero.models import VehicleKey
from recallzero.pipeline import enrich_and_cluster

app = FastAPI(title="RecallZero API", version="0.1.0")
client = NHTSAClient()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/vehicles/{year}/{make}/{model}/complaints")
def vehicle_complaints(year: int, make: str, model: str):
    try:
        complaints = client.complaints_by_vehicle(VehicleKey(make=make, model=model, model_year=year))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"NHTSA request failed: {exc}") from exc
    return {"count": len(complaints), "results": [c.model_dump(mode="json") for c in complaints]}


@app.get("/vehicles/{year}/{make}/{model}/signals")
def vehicle_signals(year: int, make: str, model: str):
    try:
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
