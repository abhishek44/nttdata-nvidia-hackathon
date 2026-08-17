from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, HTTPException, Request

from recallzero import __version__
from recallzero.backtest import RecallTimeMachine
from recallzero.config import Settings
from recallzero.demo import build_demo_records
from recallzero.investigation import EngineeringBriefRenderer
from recallzero.models import AnalyzeRequest, BacktestRequest, IngestRequest
from recallzero.pipeline import build_pipeline

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "version": __version__,
        "nim_requested": settings.use_nim,
        "nim_credentials_present": bool(settings.nvidia_api_key),
        "data_dir": str(settings.data_dir),
    }


@router.get("/api/v1/demo")
async def offline_demo() -> dict[str, object]:
    """Run a synthetic end-to-end analysis without NHTSA or NVIDIA access."""
    vehicle, complaints, recalls, target, official_date = build_demo_records()
    with TemporaryDirectory(prefix="recallzero-demo-") as tmp:
        settings = Settings(data_dir=Path(tmp), use_nim=False)
        pipeline = build_pipeline(settings, use_nim=False)
        signatures = await pipeline.extractor.extract_many(complaints)
        run = await pipeline.analyze_records(
            vehicle=vehicle,
            complaints=complaints,
            recalls=recalls,
            cutoff_date=official_date - timedelta(days=1),
            signatures=signatures,
            save=False,
        )
        backtest_result = await RecallTimeMachine(pipeline, target_match_threshold=0.12).run(
            vehicle=vehicle,
            complaints=complaints,
            recalls=recalls,
            target_recall=target,
            official_recall_date=official_date,
            replay_start_date=official_date - timedelta(days=101),
            alert_threshold=55,
            minimum_evidence=4,
            save=False,
        )
    return {
        "notice": "Synthetic installation smoke test only; not a real safety or recall result.",
        "analysis": run.model_dump(mode="json"),
        "backtest": backtest_result.model_dump(mode="json"),
    }


@router.post("/api/v1/ingest")
async def ingest(payload: IngestRequest, request: Request) -> dict[str, object]:
    pipeline = request.app.state.pipeline
    try:
        complaints, recalls = await pipeline.ingest(payload.to_vehicle(), refresh=payload.refresh)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"NHTSA ingestion failed: {exc}") from exc
    return {
        "vehicle": payload.to_vehicle().model_dump(mode="json"),
        "complaint_count": len(complaints),
        "recall_count": len(recalls),
        "complaint_date_min": min((item.received_date for item in complaints), default=None),
        "complaint_date_max": max((item.received_date for item in complaints), default=None),
    }


@router.post("/api/v1/analyze")
async def analyze(payload: AnalyzeRequest, request: Request) -> dict[str, object]:
    base_pipeline = request.app.state.pipeline
    pipeline = (
        base_pipeline
        if payload.use_nim is None
        else build_pipeline(request.app.state.settings, use_nim=payload.use_nim)
    )
    try:
        run = await pipeline.analyze_vehicle(
            payload.to_vehicle(),
            cutoff_date=payload.cutoff_date,
            refresh=payload.refresh,
            max_complaints=payload.max_complaints,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Vehicle analysis failed: {exc}") from exc
    return run.model_dump(mode="json")


@router.post("/api/v1/backtests")
async def backtest(payload: BacktestRequest, request: Request) -> dict[str, object]:
    base_pipeline = request.app.state.pipeline
    pipeline = (
        base_pipeline
        if payload.use_nim is None
        else build_pipeline(request.app.state.settings, use_nim=payload.use_nim)
    )
    vehicle = payload.to_vehicle()
    try:
        complaints, recalls = await pipeline.ingest(vehicle, refresh=payload.refresh)
        campaign = payload.target_campaign_number.upper().replace("-", "")
        target = next((item for item in recalls if item.campaign_number == campaign), None)
        if target is None:
            target = await pipeline.nhtsa.fetch_campaign(campaign)
        if target is None:
            raise HTTPException(
                status_code=404,
                detail=f"Campaign {campaign} was not found in NHTSA results; target recall text is required for post-hoc evaluation.",
            )
        machine = RecallTimeMachine(pipeline)
        result = await machine.run(
            vehicle=vehicle,
            complaints=complaints,
            recalls=recalls,
            target_recall=target,
            official_recall_date=payload.official_recall_date,
            replay_start_date=payload.replay_start_date,
            alert_threshold=payload.alert_threshold,
            minimum_evidence=payload.minimum_evidence,
            use_signature_cache=not payload.refresh,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Backtest failed: {exc}") from exc
    return result.model_dump(mode="json")


@router.get("/api/v1/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict[str, object]:
    repository = request.app.state.pipeline.repository
    run = repository.load_analysis_run(run_id)
    if run is not None:
        return run.model_dump(mode="json")
    backtest = repository.load_backtest(run_id)
    if backtest is not None:
        return backtest.model_dump(mode="json")
    raise HTTPException(status_code=404, detail="Run not found")


@router.get("/api/v1/evidence/{odi_number}")
async def get_evidence(odi_number: str, request: Request) -> dict[str, object]:
    complaint = request.app.state.pipeline.repository.find_complaint(odi_number)
    if complaint is None:
        raise HTTPException(status_code=404, detail="Complaint not found in local cache")
    return complaint.model_dump(mode="json")


@router.get("/api/v1/runs/{run_id}/signals/{signal_id}/brief", response_model=None)
async def get_brief(run_id: str, signal_id: str, request: Request) -> dict[str, str]:
    run = request.app.state.pipeline.repository.load_analysis_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Analysis run not found")
    signal = next((item for item in run.signals if item.signal_id == signal_id), None)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    return {"markdown": EngineeringBriefRenderer().render_markdown(signal)}
