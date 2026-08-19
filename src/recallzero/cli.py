from __future__ import annotations

import asyncio
import importlib.util
import json
import platform
import shutil
import sys
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from collections.abc import Awaitable
from typing import Annotated

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from recallzero import __version__
from recallzero.backtest import RecallTimeMachine
from recallzero.benchmark import (
    BenchmarkSplit,
    compare_benchmark_runs,
    create_benchmark_lock,
    preflight_benchmark,
    run_benchmark,
    verify_benchmark_lock,
    write_benchmark_csv,
)
from recallzero.benchmark_adjudication import (
    AdjudicatedBenchmarkRun,
    adjudicate_benchmark,
    build_validation_report,
    write_validation_report_csv,
)
from recallzero.benchmark_attribution import run_attribution_experiment
from recallzero.config import get_settings
from recallzero.demo import build_demo_records
from recallzero.freeze import verify_freeze
from recallzero.investigation import EngineeringBriefRenderer
from recallzero.models import Vehicle
from recallzero.pipeline import build_pipeline
from recallzero.utils import configure_logging, dumps_json

app = typer.Typer(no_args_is_help=True, help="RecallZero vehicle-defect early-warning toolkit.")
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"RecallZero {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show the installed version."),
    ] = False,
) -> None:
    """RecallZero command-line interface."""


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


def _run_cli_async(awaitable: Awaitable[None], operation: str) -> None:
    try:
        asyncio.run(awaitable)
    except typer.BadParameter:
        raise
    except Exception as exc:
        console.print(f"[red]{operation} failed:[/red] {exc}")
        raise typer.Exit(code=1) from None


def print_run(run) -> None:
    console.print(f"\n[bold]Run:[/bold] {run.run_id}")
    console.print(f"[bold]Vehicle:[/bold] {run.vehicle.display_name}")
    console.print(
        f"[bold]Complaints:[/bold] {run.complaint_count}  "
        f"[bold]Clusters:[/bold] {run.cluster_count}  "
        f"[bold]Meta signals:[/bold] {run.meta_signal_count}  "
        f"[bold]Visible recalls:[/bold] {run.recall_count_visible}  "
        f"[bold]Semantic quality:[/bold] {run.semantic_quality}"
    )
    diagnostics = run.clustering_diagnostics or {}
    if diagnostics:
        groups = diagnostics.get("component_groups") or {}
        group_summary = ", ".join(
            f"{name}:{values.get('count', 0)}" for name, values in sorted(groups.items())
        )
        console.print(
            f"[dim]Clustering: {diagnostics.get('algorithm')} eps={diagnostics.get('eps')} "
            f"largest_cluster_share={diagnostics.get('largest_cluster_share')} "
            f"groups=[{group_summary}][/dim]"
        )
    table = Table(title="Defect signals")
    table.add_column("Alert")
    table.add_column("Risk", justify="right")
    table.add_column("Scope")
    table.add_column("Issue")
    table.add_column("Evidence", justify="right")
    table.add_column("Trend", justify="right")
    table.add_column("Recall")
    for signal in run.signals:
        table.add_row(
            "YES" if signal.risk.alert else "no",
            f"{signal.risk.level.value} {signal.risk.final_score:.1f}",
            signal.signal_scope,
            signal.cluster.label,
            str(signal.cluster.evidence_count),
            f"{signal.trend.trend_ratio:.2f}x",
            signal.recall_match.campaign_number or "none",
        )
    console.print(table)
    for warning in run.warnings:
        console.print(f"[yellow]Warning:[/yellow] {warning}")


@app.command()
def doctor(
    probe_nim: Annotated[bool, typer.Option("--probe-nim", help="Send a minimal live request to the configured NIM.")] = False,
) -> None:
    """Inspect the local, NVIDIA, and Agent Toolkit configuration."""
    settings = get_settings()
    configure_logging(settings.log_level)
    table = Table(title=f"RecallZero {__version__} environment")
    table.add_column("Check")
    table.add_column("Value")
    table.add_column("Status")
    nat_installed = bool(importlib.util.find_spec("nat"))
    legacy_aiq_installed = bool(importlib.util.find_spec("aiq"))
    nat_cli = shutil.which("nat")
    rows = [
        ("Python", platform.python_version(), "[green]OK[/green]" if sys.version_info >= (3, 11) else "[yellow]CHECK[/yellow]"),
        ("Platform", platform.platform(), "[green]OK[/green]"),
        ("Data directory", str(settings.data_dir.resolve()), "[green]OK[/green]" if settings.data_dir.exists() else "[yellow]CHECK[/yellow]"),
        ("NVIDIA API key", "configured" if settings.nvidia_api_key else "missing", "[green]OK[/green]" if settings.nvidia_api_key else "[yellow]CHECK[/yellow]"),
        ("Chat NIM base URL", settings.nim_base_url, "[green]OK[/green]"),
        ("Embedding NIM base URL", settings.embedding_base_url or settings.nim_base_url, "[green]OK[/green]"),
        ("LLM model", settings.llm_model, "[green]OK[/green]"),
        ("Embedding model", settings.embedding_model, "[green]OK[/green]"),
        ("NeMo Agent Toolkit module", "installed" if nat_installed else "not installed", "[green]OK[/green]" if nat_installed else "[yellow]CHECK[/yellow]"),
        ("NeMo Agent Toolkit CLI", nat_cli or "not found on PATH", "[green]OK[/green]" if nat_cli else "[yellow]CHECK[/yellow]"),
        (
            "Legacy aiq compatibility",
            "installed" if legacy_aiq_installed else "not installed (not required)",
            "[green]OK[/green]" if legacy_aiq_installed else "[cyan]OPTIONAL[/cyan]",
        ),
    ]
    for name, value, status in rows:
        table.add_row(name, str(value), status)
    console.print(table)
    if not legacy_aiq_installed:
        console.print(
            "[dim]The deprecated aiq module is optional. Current AI-Q Blueprint and RecallZero integrations use "
            "the nat module/CLI from nvidia-nat.[/dim]"
        )

    if probe_nim:
        async def _probe() -> None:
            from recallzero.intelligence import NIMClient

            client = NIMClient(
                api_key=settings.nvidia_api_key,
                base_url=settings.nim_base_url,
                timeout_seconds=settings.request_timeout_seconds,
                max_retries=1,
                retry_base_delay_seconds=settings.retry_base_delay_seconds,
                retry_max_delay_seconds=settings.retry_max_delay_seconds,
            )
            text = await client.chat_completion(
                model=settings.llm_model,
                messages=[{"role": "user", "content": "Reply with exactly: RECALLZERO_OK"}],
                max_tokens=64,
                disable_thinking=True,
            )
            console.print(f"NIM response: [bold]{text}[/bold]")

        _run_cli_async(_probe(), "NIM probe")


@app.command()
def fetch(
    make: str = typer.Option(...),
    model: str = typer.Option(...),
    years: str = typer.Option(..., help="Comma-separated model years, for example 2021,2022."),
    refresh: bool = typer.Option(False, help="Ignore the local cache."),
) -> None:
    """Fetch and normalize NHTSA complaints and recalls."""
    settings = get_settings()
    configure_logging(settings.log_level)
    vehicle = Vehicle(make=make, model=model, model_years=parse_years(years))

    async def _run() -> None:
        pipeline = build_pipeline(settings, use_nim=False)
        complaints, recalls = await pipeline.ingest(vehicle, refresh=refresh)
        console.print(f"Fetched [bold]{len(complaints)}[/bold] complaints and [bold]{len(recalls)}[/bold] recalls.")
        console.print(f"Normalized data: {pipeline.repository.complaints_path(vehicle)}")

    _run_cli_async(_run(), "NHTSA fetch")


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
    brief: bool = typer.Option(False, help="Print a deterministic brief for the highest-ranked signal."),
) -> None:
    """Run an end-to-end vehicle investigation."""
    settings = get_settings()
    configure_logging(settings.log_level)
    vehicle = Vehicle(make=make, model=model, model_years=parse_years(years))

    async def _run() -> None:
        pipeline = build_pipeline(settings, use_nim=not no_nim)
        run = await pipeline.analyze_vehicle(
            vehicle,
            cutoff_date=parse_iso_date(cutoff, "--cutoff"),
            refresh=refresh,
            max_complaints=max_complaints,
        )
        print_run(run)
        if json_output:
            json_output.parent.mkdir(parents=True, exist_ok=True)
            json_output.write_text(dumps_json(run.model_dump(mode="json")), encoding="utf-8")
            console.print(f"Saved JSON: {json_output}")
        if brief and run.signals:
            console.print(EngineeringBriefRenderer().render_markdown(run.signals[0]))

    _run_cli_async(_run(), "Vehicle analysis")


@app.command()
def backtest(
    make: str = typer.Option(...),
    model: str = typer.Option(...),
    years: str = typer.Option(...),
    campaign: str = typer.Option(...),
    recall_date: str = typer.Option(..., help="Official recall date in YYYY-MM-DD format."),
    replay_start: str | None = typer.Option(None, help="Optional replay start in YYYY-MM-DD format."),
    threshold: float | None = typer.Option(None, min=0, max=100),
    minimum_evidence: int | None = typer.Option(None, min=1),
    refresh: bool = typer.Option(False),
    no_nim: bool = typer.Option(False),
    json_output: Path | None = typer.Option(None, "--json"),
) -> None:
    """Replay only information available before a known historical recall."""
    settings = get_settings()
    configure_logging(settings.log_level)
    vehicle = Vehicle(make=make, model=model, model_years=parse_years(years))

    async def _run() -> None:
        pipeline = build_pipeline(settings, use_nim=not no_nim)
        complaints, recalls = await pipeline.ingest(vehicle, refresh=refresh)
        clean_campaign = campaign.upper().replace("-", "")
        target = next((item for item in recalls if item.campaign_number == clean_campaign), None)
        if target is None:
            target = await pipeline.nhtsa.fetch_campaign(clean_campaign)
        if target is None:
            raise typer.BadParameter(f"Campaign {clean_campaign} could not be retrieved from NHTSA")
        result = await RecallTimeMachine(pipeline).run(
            vehicle=vehicle,
            complaints=complaints,
            recalls=recalls,
            target_recall=target,
            official_recall_date=parse_iso_date(recall_date, "--recall-date"),
            replay_start_date=parse_iso_date(replay_start, "--replay-start"),
            alert_threshold=threshold,
            minimum_evidence=minimum_evidence,
            use_signature_cache=not refresh,
        )
        console.print(f"[bold]Status:[/bold] {result.status}")
        console.print(f"[bold]First any alert:[/bold] {result.first_any_alert_date or 'none'}")
        console.print(
            f"[bold]Earliest target-like candidate:[/bold] "
            f"{result.earliest_target_like_candidate_date or 'none'}"
        )
        console.print(f"[bold]First qualified alert:[/bold] {result.first_qualified_alert_date or 'none'}")
        console.print(f"[bold]First matching alert:[/bold] {result.first_matching_alert_date or 'none'}")
        console.print(f"[bold]Lead time:[/bold] {result.lead_time_days if result.lead_time_days is not None else 'not measured'}")
        console.print(f"[bold]Best post-hoc pre-alert target score:[/bold] {result.max_pre_alert_target_score if result.max_pre_alert_target_score is not None else 'none'}")
        console.print(f"[bold]Best post-hoc candidate date:[/bold] {result.max_pre_alert_target_date or 'none'}")
        console.print(f"[bold]Anti-leakage checks:[/bold] {json.dumps(result.anti_leakage_checks)}")
        for warning in result.warnings:
            console.print(f"[yellow]Warning:[/yellow] {warning}")
        if json_output:
            json_output.parent.mkdir(parents=True, exist_ok=True)
            json_output.write_text(dumps_json(result.model_dump(mode="json")), encoding="utf-8")

    _run_cli_async(_run(), "Historical backtest")


@app.command()
def demo(backtest_mode: bool = typer.Option(True, "--backtest/--analysis-only")) -> None:
    """Run the complete offline synthetic demo without NHTSA or NVIDIA access."""
    settings = get_settings()
    configure_logging(settings.log_level)

    async def _run() -> None:
        console.print("[dim]Offline demo: NVIDIA NIM is intentionally disabled, so heuristic signatures are expected.[/dim]")
        vehicle, complaints, recalls, target, official_date = build_demo_records()
        with TemporaryDirectory(prefix="recallzero-demo-") as tmp:
            demo_settings = settings.model_copy(update={"data_dir": Path(tmp), "use_nim": False})
            pipeline = build_pipeline(demo_settings, use_nim=False)
            run = await pipeline.analyze_records(
                vehicle=vehicle,
                complaints=complaints,
                recalls=recalls,
                cutoff_date=official_date.replace(day=9),
                save=False,
            )
            print_run(run)
            if backtest_mode:
                result = await RecallTimeMachine(pipeline, target_match_threshold=0.12).run(
                    vehicle=vehicle,
                    complaints=complaints,
                    recalls=recalls,
                    target_recall=target,
                    official_recall_date=official_date,
                    replay_start_date=date(2022, 3, 1),
                    alert_threshold=55,
                    minimum_evidence=4,
                    save=False,
                )
                console.print(
                    f"\n[bold]Synthetic Time Machine:[/bold] {result.status}; "
                    f"first alert={result.first_matching_alert_date}; lead={result.lead_time_days} days"
                )

    _run_cli_async(_run(), "Synthetic demo")


@app.command("freeze-verify")
def freeze_verify(
    manifest: Path = typer.Option(
        Path("benchmarks/detector_freeze_v1.yaml"),
        "--manifest",
        help="Detector freeze manifest to verify against live source and runtime configuration.",
    ),
) -> None:
    """Verify Detector Freeze v1 source hashes and live loaded configuration."""
    settings = get_settings()
    verification = verify_freeze(manifest, settings)
    table = Table(title=f"Detector freeze: {verification.freeze_id}")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Expected")
    table.add_column("Actual")
    for item in verification.checks:
        table.add_row(
            item.name,
            "[green]PASS[/green]" if item.ok else "[red]FAIL[/red]",
            item.expected,
            item.actual,
        )
    console.print(table)
    if not verification.ok:
        console.print("[red]Freeze verification failed. Benchmark comparability is invalid.[/red]")
        raise typer.Exit(code=1)
    console.print("[green]Freeze verified against live Settings/risk.yml and source hashes.[/green]")


@app.command("benchmark-preflight")
def benchmark_preflight(
    manifest: Path = typer.Option(
        Path("config/candidates.yml"),
        "--manifest",
        help="Preregistered benchmark case manifest.",
    ),
    freeze_manifest: Path = typer.Option(
        Path("benchmarks/detector_freeze_v1.yaml"),
        "--freeze",
        help="Detector freeze manifest to verify before checking case metadata.",
    ),
    split: BenchmarkSplit = typer.Option("validation", "--split"),
    refresh: bool = typer.Option(False, help="Refresh NHTSA case metadata; detector scoring is never run."),
    json_output: Path | None = typer.Option(None, "--json"),
) -> None:
    """Validate benchmark identity/data prerequisites without running the detector."""
    settings = get_settings()
    configure_logging(settings.log_level)

    async def _run() -> None:
        result = await preflight_benchmark(
            settings=settings,
            candidates_path=manifest,
            freeze_manifest_path=freeze_manifest,
            split=split,
            refresh=refresh,
        )
        table = Table(title=f"Benchmark preflight: {result.benchmark_id} [{split}]")
        table.add_column("Case")
        table.add_column("Role")
        table.add_column("Eligible complaints", justify="right")
        table.add_column("Complaint queries", justify="right")
        table.add_column("Recalls", justify="right")
        table.add_column("Status")
        for item in result.cases:
            table.add_row(
                item.name,
                item.expected_role,
                str(item.complaint_count_eligible),
                str(len(item.complaint_models_queried)),
                str(item.recall_count_raw),
                "[green]PASS[/green]" if item.valid else "[red]FAIL[/red]",
            )
            if len(item.complaint_models_queried) > 1:
                details = ", ".join(
                    f"{key}={count}"
                    for key, count in sorted(item.complaint_count_by_model_variant.items())
                )
                console.print(
                    f"[dim]{item.name} complaint variants:[/dim] "
                    f"{', '.join(item.complaint_models_queried)}"
                    + (f" ({details})" if details else "")
                )
            for warning in item.warnings:
                console.print(f"[yellow]{item.name} warning:[/yellow] {warning}")
            for error in item.errors:
                console.print(f"[red]{item.name} error:[/red] {error}")
        console.print(table)
        console.print(
            f"Freeze={'PASS' if result.freeze_verified else 'FAIL'}; cases={result.case_count}; "
            f"positives={result.positive_count}; controls={result.control_count}; "
            f"manufacturers={result.distinct_manufacturers}; strata={result.distinct_selection_strata}"
        )
        if json_output is not None:
            json_output.parent.mkdir(parents=True, exist_ok=True)
            json_output.write_text(dumps_json(result.model_dump(mode="json")), encoding="utf-8")
            console.print(f"Saved preflight JSON: {json_output}")
        if not result.ready:
            console.print("[red]Benchmark preflight is not ready. Do not lock or score this cohort.[/red]")
            raise typer.Exit(code=1)
        console.print("[green]READY FOR VALIDATION[/green]")

    _run_cli_async(_run(), "Benchmark preflight")


@app.command("benchmark-lock")
def benchmark_lock(
    manifest: Path = typer.Option(Path("config/candidates.yml"), "--manifest"),
    freeze_manifest: Path = typer.Option(
        Path("benchmarks/detector_freeze_v1.yaml"), "--freeze"
    ),
    split: BenchmarkSplit = typer.Option("validation", "--split"),
    output: Path = typer.Option(..., "--output"),
) -> None:
    """Lock the exact candidate manifest and selected split before detector scoring."""
    lock = create_benchmark_lock(
        candidates_path=manifest,
        freeze_manifest_path=freeze_manifest,
        split=split,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(dumps_json(lock.model_dump(mode="json")), encoding="utf-8")
    verification = verify_benchmark_lock(
        lock_path=output,
        candidates_path=manifest,
        freeze_manifest_path=freeze_manifest,
        split=split,
    )
    if not verification.ok:
        console.print("[red]Lock verification failed immediately after creation.[/red]")
        raise typer.Exit(code=1)
    console.print(
        f"Locked [bold]{lock.case_count}[/bold] {split} cases "
        f"({lock.positive_count} positive, {lock.control_count} control)."
    )
    console.print(f"Manifest SHA-256: {lock.candidate_manifest_sha256}")
    console.print(f"Saved lock: {output}")


@app.command()
def benchmark(
    manifest: Path = typer.Option(
        Path("config/candidates.yml"),
        "--manifest",
        help="Manifest of positive/control benchmark cases.",
    ),
    freeze_manifest: Path = typer.Option(
        Path("benchmarks/detector_freeze_v1.yaml"),
        "--freeze",
        help="Detector freeze manifest that must match before the benchmark runs.",
    ),
    lock: Path | None = typer.Option(
        None,
        "--lock",
        help="Preregistered benchmark lock. Required for validation and holdout splits.",
    ),
    split: BenchmarkSplit = typer.Option("development", "--split"),
    confirm_holdout: bool = typer.Option(
        False,
        "--confirm-holdout",
        help="Required acknowledgement before scoring holdout cases.",
    ),
    json_output: Path = typer.Option(
        Path("data/runs/benchmark_v1.json"),
        "--json",
        help="Raw detector benchmark result. Control alerts are not adjudicated here.",
    ),
    csv_output: Path | None = typer.Option(
        Path("data/runs/benchmark_v1.csv"),
        "--csv",
        help="Optional per-case raw CSV summary.",
    ),
    refresh: bool = typer.Option(False, help="Refresh NHTSA data/signatures instead of reusing local evidence."),
    allow_freeze_mismatch: bool = typer.Option(
        False,
        "--allow-freeze-mismatch",
        help="Run despite a freeze mismatch. Results are invalid/non-comparable; not recommended.",
    ),
) -> None:
    """Run a split-aware frozen-detector benchmark and persist raw detector output."""
    if split == "holdout" and not confirm_holdout:
        console.print(
            "[red]Holdout execution requires --confirm-holdout. "
            "Holdout cases should not be inspected during detector development.[/red]"
        )
        raise typer.Exit(code=2)
    settings = get_settings()
    configure_logging(settings.log_level)

    async def _run() -> None:
        result, verification = await run_benchmark(
            settings=settings,
            candidates_path=manifest,
            freeze_manifest_path=freeze_manifest,
            split=split,
            lock_path=lock,
            refresh=refresh,
            allow_freeze_mismatch=allow_freeze_mismatch,
            confirm_holdout=confirm_holdout,
        )
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(dumps_json(result.model_dump(mode="json")), encoding="utf-8")
        if csv_output is not None:
            write_benchmark_csv(result, csv_output)

        console.print(f"[bold]Benchmark:[/bold] {result.benchmark_run_id}")
        console.print(f"[bold]Split:[/bold] {result.benchmark_split}")
        console.print(f"[bold]Freeze:[/bold] {result.freeze_id} ({'verified' if verification.ok else 'MISMATCH'})")
        console.print(f"[bold]Manifest lock:[/bold] {'verified' if result.lock_verified else 'not used'}")
        console.print(f"[bold]Cases:[/bold] {result.case_count}")
        console.print(f"[bold]Raw aggregate:[/bold] {json.dumps(result.aggregate, sort_keys=True)}")
        console.print("[yellow]Control alerts remain unadjudicated in this raw artifact.[/yellow]")
        console.print(f"Saved raw JSON: {json_output}")
        if csv_output is not None:
            console.print(f"Saved raw CSV: {csv_output}")

    _run_cli_async(_run(), "Benchmark")


@app.command("benchmark-attribution-experiment")
def benchmark_attribution_experiment(
    raw_result: Path = typer.Argument(..., exists=True, readable=True),
    manifest: Path = typer.Option(Path("config/candidates.yml"), "--manifest"),
    json_output: Path = typer.Option(
        Path("data/runs/target_attribution_experiment_a.json"),
        "--json",
        help="Development-only TF-IDF vs NIM-embedding attribution comparison.",
    ),
    top_target_count: int = typer.Option(
        10,
        "--top-target-count",
        min=1,
        max=50,
        help="Per unmatched positive case, include this many highest baseline target candidates plus every alert candidate.",
    ),
    target_match_threshold: float = typer.Option(
        0.45,
        "--target-match-threshold",
        min=0.0,
        max=1.0,
        help="Evaluation-only qualification threshold; keep at the frozen value for Experiment A.",
    ),
) -> None:
    """Run isolated Target Attribution Experiment A without rerunning Detector v1."""
    settings = get_settings()
    configure_logging(settings.log_level)

    async def _run() -> None:
        result = await run_attribution_experiment(
            settings=settings,
            raw_result_path=raw_result,
            candidates_path=manifest,
            target_match_threshold=target_match_threshold,
            top_target_count=top_target_count,
        )
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(dumps_json(result.model_dump(mode="json")), encoding="utf-8")
        console.print(f"[bold]Experiment:[/bold] {result.experiment_id}")
        console.print("[bold]Scope:[/bold] development diagnostic only")
        console.print(f"[bold]Source benchmark:[/bold] {result.source_benchmark_run_id}")
        console.print(f"[bold]Cases:[/bold] {result.case_count}  [bold]Candidates:[/bold] {result.candidate_count}")
        console.print(
            f"[bold]New work:[/bold] 0 LLM calls, 0 detector replays, "
            f"~{result.estimated_embedding_api_calls} embedding API call(s) "
            f"for {result.unique_text_count} unique texts"
        )
        console.print(
            f"[bold]Alert candidates qualifying target:[/bold] "
            f"baseline={result.baseline_qualified_alert_count}, "
            f"embedding-experiment={result.experimental_qualified_alert_count}"
        )
        console.print(
            "[yellow]Interpret discrimination, not score inflation: unrelated alert candidates should remain below the target threshold.[/yellow]"
        )
        console.print(f"Saved attribution experiment JSON: {json_output}")

    _run_cli_async(_run(), "Target attribution experiment")


@app.command("benchmark-adjudicate")
def benchmark_adjudicate(
    raw_result: Path = typer.Argument(..., exists=True, readable=True),
    manifest: Path = typer.Option(Path("config/candidates.yml"), "--manifest"),
    freeze_manifest: Path | None = typer.Option(None, "--freeze"),
    json_output: Path = typer.Option(..., "--json"),
    refresh: bool = typer.Option(False, help="Refresh recall data used only for post-hoc adjudication."),
) -> None:
    """Classify frozen alert lineages against visible/future recalls after scoring."""
    settings = get_settings()
    configure_logging(settings.log_level)

    async def _run() -> None:
        result = await adjudicate_benchmark(
            settings=settings,
            raw_result_path=raw_result,
            candidates_path=manifest,
            freeze_manifest_path=freeze_manifest,
            refresh=refresh,
        )
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(dumps_json(result.model_dump(mode="json")), encoding="utf-8")
        console.print(f"[bold]Adjudicated benchmark:[/bold] {result.benchmark_run_id}")
        console.print(f"[bold]Aggregate:[/bold] {json.dumps(result.aggregate, sort_keys=True)}")
        console.print(f"Saved adjudicated JSON: {json_output}")

    _run_cli_async(_run(), "Benchmark adjudication")


@app.command("benchmark-report")
def benchmark_report(
    adjudicated_result: Path = typer.Argument(..., exists=True, readable=True),
    json_output: Path | None = typer.Option(None, "--json"),
    csv_output: Path | None = typer.Option(None, "--csv"),
) -> None:
    """Render final detector-validation summary from an adjudicated benchmark."""
    result = AdjudicatedBenchmarkRun.model_validate_json(
        adjudicated_result.read_text(encoding="utf-8")
    )
    report = build_validation_report(result)
    if json_output is not None:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(dumps_json(report), encoding="utf-8")
        console.print(f"Saved validation summary JSON: {json_output}")
    if csv_output is not None:
        write_validation_report_csv(result, csv_output)
        console.print(f"Saved validation summary CSV: {csv_output}")
    if json_output is None:
        console.print(dumps_json(report))
    console.print(
        f"[bold]Prototype acceptance:[/bold] "
        f"{'PASS' if result.aggregate.get('prototype_acceptance_pass') else 'FAIL'}"
    )


@app.command("benchmark-compare")
def benchmark_compare(
    left: Path = typer.Argument(..., exists=True, readable=True),
    right: Path = typer.Argument(..., exists=True, readable=True),
    json_output: Path | None = typer.Option(None, "--json", help="Optional path for the comparison JSON."),
) -> None:
    """Compare two benchmark outputs, including snapshot risk-factor score deltas."""
    left_data = json.loads(left.read_text(encoding="utf-8"))
    right_data = json.loads(right.read_text(encoding="utf-8"))
    comparison = compare_benchmark_runs(left_data, right_data)
    rendered = dumps_json(comparison)
    if json_output:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(rendered, encoding="utf-8")
        console.print(f"Saved comparison JSON: {json_output}")
    else:
        console.print(rendered)


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0"),
    port: int = typer.Option(8080),
    reload: bool = typer.Option(False),
) -> None:
    """Start the FastAPI service and Safety Radar dashboard."""
    uvicorn.run("recallzero.api.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
