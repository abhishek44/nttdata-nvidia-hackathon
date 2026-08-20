from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import date
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from recallzero.config import get_settings
from recallzero.detector_v2 import build_detector_v2
from recallzero.investigation.brief_v2 import EngineeringBriefRendererV2
from recallzero.models import Vehicle
from recallzero.utils import configure_logging, dumps_json

app = typer.Typer(
    no_args_is_help=True,
    help=(
        "RecallZero Detector v2 - experimental six-factor risk scoring with 30/90 trend "
        "windows, model-year/VIN/vehicle breadth, and manufacturer-wide watch. "
        "Detector v1 remains frozen and unchanged."
    ),
)
console = Console()


def parse_years(value: str) -> tuple[int, ...]:
    try:
        years = tuple(sorted({int(item.strip()) for item in value.split(",") if item.strip()}))
    except ValueError as exc:
        raise typer.BadParameter("Years must be comma-separated integers") from exc
    if not years:
        raise typer.BadParameter("At least one model year is required")
    return years


def parse_iso_date(value: str | None, option_name: str) -> date | None:
    if value is None or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise typer.BadParameter(f"{option_name} must use YYYY-MM-DD format") from exc


def _run(awaitable: Awaitable[None], operation: str) -> None:
    try:
        asyncio.run(awaitable)
    except typer.BadParameter:
        raise
    except Exception as exc:  # pragma: no cover - CLI guard
        console.print(f"[red]{operation} failed:[/red] {exc}")
        raise typer.Exit(code=1) from None


def print_result(result) -> None:
    run = result.run
    console.print(f"\n[bold]Detector v2 run:[/bold] {run.run_id}")
    console.print(f"[bold]Vehicle:[/bold] {run.vehicle.display_name}")
    console.print(
        f"[bold]Complaints:[/bold] {run.complaint_count}  "
        f"[bold]Signals:[/bold] {len(result.signals_v2)}  "
        f"[bold]Trend windows:[/bold] {result.trend_recent_window_days}/{result.trend_baseline_window_days}  "
        f"[bold]Semantic quality:[/bold] {run.semantic_quality}"
    )
    table = Table(title="Detector v2 signals (six-factor risk)")
    table.add_column("Alert")
    table.add_column("v2 Risk", justify="right")
    table.add_column("Scope")
    table.add_column("Issue")
    table.add_column("Evidence", justify="right")
    table.add_column("Trend", justify="right")
    table.add_column("MY span", justify="right")
    table.add_column("VINpfx", justify="right")
    table.add_column("Recall")
    for item in result.signals_v2:
        signal = item.signal
        table.add_row(
            "YES" if item.risk.alert else "no",
            f"{item.risk.level.value} {item.risk.final_score:.1f}",
            signal.signal_scope,
            signal.cluster.label,
            str(signal.cluster.evidence_count),
            f"{signal.trend.trend_ratio:.2f}x",
            str(item.coverage.distinct_model_years),
            str(item.coverage.distinct_vin_prefixes),
            signal.recall_match.campaign_number or "none",
        )
    console.print(table)
    for warning in run.warnings:
        console.print(f"[yellow]Warning:[/yellow] {warning}")


@app.command()
def analyze(
    make: str = typer.Option(...),
    model: str = typer.Option(...),
    years: str = typer.Option(...),
    cutoff: str | None = typer.Option(None, help="Evidence cutoff in YYYY-MM-DD format."),
    refresh: bool = typer.Option(False),
    no_nim: bool = typer.Option(False, help="Force heuristic extraction and TF-IDF clustering."),
    max_complaints: int | None = typer.Option(None, min=1),
    json_output: Path | None = typer.Option(None, "--json", help="Optional path for the complete JSON result."),
    brief: bool = typer.Option(False, help="Print a Detector v2 brief for the highest-ranked signal."),
) -> None:
    """Run a Detector v2 end-to-end vehicle investigation."""
    settings = get_settings()
    configure_logging(settings.log_level)
    vehicle = Vehicle(make=make, model=model, model_years=parse_years(years))

    async def _go() -> None:
        detector = build_detector_v2(settings, use_nim=not no_nim)
        result = await detector.analyze_vehicle(
            vehicle,
            cutoff_date=parse_iso_date(cutoff, "--cutoff"),
            refresh=refresh,
            max_complaints=max_complaints,
        )
        print_result(result)
        if json_output:
            json_output.parent.mkdir(parents=True, exist_ok=True)
            json_output.write_text(dumps_json(result.model_dump(mode="json")), encoding="utf-8")
            console.print(f"Saved JSON: {json_output}")
        if brief and result.signals_v2:
            console.print(EngineeringBriefRendererV2().render_markdown(result.signals_v2[0]))

    _run(_go(), "Detector v2 analysis")


@app.command("fleet-watch")
def fleet_watch(
    config: Path = typer.Option(Path("config/manufacturer_watch.yml"), "--config", help="Manufacturer watch config."),
    refresh: bool = typer.Option(False),
    no_nim: bool = typer.Option(False),
    max_complaints: int | None = typer.Option(None, min=1),
    json_output: Path | None = typer.Option(None, "--json"),
) -> None:
    """Run the manufacturer-wide watch (roadmap capability) across a configured lineup."""
    settings = get_settings()
    configure_logging(settings.log_level)

    async def _go() -> None:
        from recallzero.fleet import run_fleet_watch

        detector = build_detector_v2(settings, use_nim=not no_nim)
        dashboard = await run_fleet_watch(
            detector, config_path=config, refresh=refresh, max_complaints=max_complaints
        )
        console.print(f"\n[bold]Manufacturer watch:[/bold] {dashboard.manufacturer} (roadmap capability)")
        table = Table(title="Cross-vehicle defect radar (sorted by v2 risk)")
        table.add_column("Vehicle")
        table.add_column("Issue")
        table.add_column("v2 Risk", justify="right")
        table.add_column("Vehicles sharing", justify="right")
        table.add_column("Evidence", justify="right")
        table.add_column("Recall")
        for row in dashboard.rows:
            table.add_row(
                row.vehicle,
                row.issue,
                f"{row.level} {row.final_score:.1f}",
                str(row.distinct_vehicles),
                str(row.evidence_count),
                row.campaign_number or "none",
            )
        console.print(table)
        if json_output:
            json_output.parent.mkdir(parents=True, exist_ok=True)
            json_output.write_text(dumps_json(dashboard.model_dump(mode="json")), encoding="utf-8")
            console.print(f"Saved JSON: {json_output}")

    _run(_go(), "Manufacturer watch")


@app.command()
def validate(
    cases: Path = typer.Option(Path("config/candidates.yml"), "--cases", help="Locked validation cohort."),
    no_nim: bool = typer.Option(False),
    refresh: bool = typer.Option(False),
    json_output: Path | None = typer.Option(None, "--json"),
) -> None:
    """Replay the locked 20-case cohort through Detector v2 and report sensitivity
    alongside the preserved Detector v1 result for comparison.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    async def _go() -> None:
        from recallzero.validation_v2 import run_validation

        detector = build_detector_v2(settings, use_nim=not no_nim)
        report = await run_validation(detector, cases_path=cases, refresh=refresh)
        console.print(f"\n[bold]Detector v2 validation[/bold] - {report.total_cases} cases")
        console.print(f"[bold]Valid cases:[/bold] {report.valid_cases}/{report.total_cases}")
        console.print(
            f"[bold]Positive sensitivity (v2):[/bold] {report.positive_detected}/{report.positive_total} "
            f"= {report.positive_sensitivity:.0%} (v1 baseline: 1/10 = 10%)"
        )
        console.print(f"[bold]Control alerts (v2):[/bold] {report.control_alerted}/{report.control_total}")
        table = Table(title="Per-case v2 validation")
        table.add_column("Case")
        table.add_column("Role")
        table.add_column("Status")
        table.add_column("Detected")
        for row in report.rows:
            table.add_row(row.name, row.expected_role, row.status, row.detected)
        console.print(table)
        for note in report.notes:
            console.print(f"[dim]{note}[/dim]")
        if json_output:
            json_output.parent.mkdir(parents=True, exist_ok=True)
            json_output.write_text(dumps_json(report.model_dump(mode="json")), encoding="utf-8")
            console.print(f"Saved JSON: {json_output}")

    _run(_go(), "Detector v2 validation")