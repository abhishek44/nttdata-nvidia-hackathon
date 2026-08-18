from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import typer

from recallzero.backtest.time_machine import run_time_machine
from recallzero.clients.nhtsa import NHTSAClient
from recallzero.intelligence.signature_extractor import SignatureExtractor
from recallzero.models import Complaint, EnrichedComplaint, FailureSignature, VehicleKey

app = typer.Typer(help="RecallZero MVP CLI")


@app.command()
def fetch(make: str, model: str, year: int, out: Path = Path("complaints.json")):
    """Fetch real complaints from the public NHTSA API."""
    rows = NHTSAClient().complaints_by_vehicle(VehicleKey(make=make, model=model, model_year=year))
    out.write_text(json.dumps([r.model_dump(mode="json") for r in rows], indent=2), encoding="utf-8")
    typer.echo(f"Wrote {len(rows)} complaints to {out}")


@app.command("demo-backtest")
def demo_backtest():
    """Run a deterministic smoke-test backtest using clearly synthetic complaints."""
    vehicle = VehicleKey(make="DEMO", model="EV", model_year=2022)
    start = date(2022, 1, 1)
    dates = [0, 10, 25, 48, 55, 60, 66, 72, 78, 84, 90, 96]
    rows = []
    for i, offset in enumerate(dates, 1):
        c = Complaint(
            odi_number=f"SYN-{i:03d}",
            vehicle=vehicle,
            date_complaint_filed=start + timedelta(days=offset),
            components=["ELECTRICAL SYSTEM", "POWER TRAIN"],
            summary="Vehicle lost motive power while driving and displayed Stop Safely Now.",
        )
        rows.append(
            EnrichedComplaint(
                complaint=c,
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
    typer.echo(result.model_dump_json(indent=2))


if __name__ == "__main__":
    app()
