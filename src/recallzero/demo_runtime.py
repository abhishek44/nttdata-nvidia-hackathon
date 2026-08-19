from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import numpy as np

from recallzero.config import Settings
from recallzero.data import FileRepository
from recallzero.intelligence import NIMClient, NIMEmbedder, NIMFailureExtractor

DEFAULT_DEMO_ODI = "11466150"


class DemoRuntimeError(RuntimeError):
    """A presentation-runtime precondition was not satisfied."""


def _endpoint_kind(base_url: str) -> str:
    host = (urlparse(base_url).hostname or "").lower()
    if host in {"integrate.api.nvidia.com", "api.nvidia.com"}:
        return "NVIDIA hosted API"
    if host in {"127.0.0.1", "localhost", "0.0.0.0"}:
        return "Local NVIDIA NIM"
    return "Configured NVIDIA-compatible endpoint"


def _client(*, settings: Settings, base_url: str) -> NIMClient:
    return NIMClient(
        api_key=settings.nvidia_api_key,
        base_url=base_url,
        timeout_seconds=settings.request_timeout_seconds,
        max_retries=settings.max_retries,
        retry_base_delay_seconds=settings.retry_base_delay_seconds,
        retry_max_delay_seconds=settings.retry_max_delay_seconds,
    )


def demo_readiness(settings: Settings, repository: FileRepository) -> dict[str, Any]:
    chat_url = settings.nim_base_url
    embedding_url = settings.embedding_base_url or settings.nim_base_url
    chat_client = _client(settings=settings, base_url=chat_url)
    embedding_client = _client(settings=settings, base_url=embedding_url)
    complaint = repository.find_complaint(DEFAULT_DEMO_ODI)

    normalized_dir = Path(settings.data_dir) / "normalized"
    complaint_files = len(list(normalized_dir.glob("*_complaints.json"))) if normalized_dir.exists() else 0

    checks = {
        "nim_extraction_configured": settings.use_nim and chat_client.configured,
        "nvidia_embeddings_configured": settings.use_nim and embedding_client.configured,
        "demo_complaint_cached": complaint is not None,
        "normalized_complaint_cache_present": complaint_files > 0,
    }
    return {
        "ready_for_live_trace": all(
            checks[name]
            for name in (
                "nim_extraction_configured",
                "nvidia_embeddings_configured",
                "demo_complaint_cached",
            )
        ),
        "checks": checks,
        "default_demo_odi": DEFAULT_DEMO_ODI,
        "cached_complaint_file_count": complaint_files,
        "services": {
            "extraction": {
                "model": settings.llm_model,
                "endpoint": chat_url,
                "endpoint_kind": _endpoint_kind(chat_url),
            },
            "embedding": {
                "model": settings.embedding_model,
                "endpoint": embedding_url,
                "endpoint_kind": _endpoint_kind(embedding_url),
            },
            "risk_engine": {
                "type": "RecallZero deterministic analytics",
                "alert_threshold": settings.risk_config().alert_threshold,
                "llm_decision": False,
            },
        },
        "note": (
            "Readiness verifies configuration and cached demo evidence only. "
            "The fresh trace itself is the live connectivity/inference proof."
        ),
    }


async def run_fresh_nvidia_trace(
    *,
    settings: Settings,
    repository: FileRepository,
    odi_number: str,
) -> dict[str, Any]:
    """Run one fresh NVIDIA extraction + embedding without writing detector caches.

    This endpoint exists only for the live presentation path. It deliberately uses
    the strict NIM extractor and NIM embedder directly, rather than the detector's
    hybrid/cache path, so a successful response proves fresh NVIDIA inference.
    """

    complaint = repository.find_complaint(odi_number)
    if complaint is None:
        raise DemoRuntimeError(
            f"ODI {odi_number} is not present in the local normalized complaint cache. "
            "Run RecallZero ingest/analyze for the vehicle before the presentation."
        )

    if not settings.use_nim:
        raise DemoRuntimeError("RECALLZERO_USE_NIM is disabled; fresh NVIDIA inference cannot run.")

    chat_url = settings.nim_base_url
    embedding_url = settings.embedding_base_url or settings.nim_base_url
    chat_client = _client(settings=settings, base_url=chat_url)
    embedding_client = _client(settings=settings, base_url=embedding_url)
    if not chat_client.configured:
        raise DemoRuntimeError("NVIDIA extraction endpoint is not configured.")
    if not embedding_client.configured:
        raise DemoRuntimeError("NVIDIA embedding endpoint is not configured.")

    extractor = NIMFailureExtractor(chat_client, settings.llm_model)
    extraction_started = time.perf_counter()
    signature = await extractor.extract(complaint)
    extraction_ms = round((time.perf_counter() - extraction_started) * 1000.0, 1)

    embedding_input = signature.canonical_text()
    embedder = NIMEmbedder(embedding_client, settings.embedding_model, batch_size=1)
    embedding_started = time.perf_counter()
    embedding_result = await embedder.embed([embedding_input])
    embedding_ms = round((time.perf_counter() - embedding_started) * 1000.0, 1)

    matrix = np.asarray(embedding_result.matrix, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[0] != 1 or matrix.shape[1] == 0:
        raise DemoRuntimeError("NVIDIA embedding response had an unexpected shape.")
    vector = matrix[0]
    preview = [round(float(value), 6) for value in vector[:8]]
    l2_norm = round(math.sqrt(float(np.dot(vector, vector))), 6)

    return {
        "fresh_inference": True,
        "cache_read": "complaint only",
        "cache_write": False,
        "detector_risk_recomputed": False,
        "complaint": {
            "odi_number": complaint.odi_number,
            "received_date": complaint.received_date.isoformat(),
            "vehicle": complaint.vehicle.model_dump(mode="json"),
            "components": list(complaint.components),
            "narrative": complaint.narrative,
            "crash": complaint.crash,
            "fire": complaint.fire,
            "injuries": complaint.injuries,
            "deaths": complaint.deaths,
        },
        "extraction": {
            "provider": "NVIDIA",
            "model": settings.llm_model,
            "endpoint": chat_url,
            "endpoint_kind": _endpoint_kind(chat_url),
            "latency_ms": extraction_ms,
            "signature": signature.model_dump(mode="json"),
        },
        "embedding": {
            "provider": "NVIDIA",
            "model": settings.embedding_model,
            "endpoint": embedding_url,
            "endpoint_kind": _endpoint_kind(embedding_url),
            "latency_ms": embedding_ms,
            "input": "FailureSignature.canonical_text()",
            "dimension": int(vector.shape[0]),
            "l2_norm": l2_norm,
            "preview": preview,
        },
        "decision_boundary": {
            "ai_used_for": [
                "complaint language understanding",
                "structured failure-signature extraction",
                "semantic vector representation",
            ],
            "ai_not_used_for": [
                "risk formula",
                "evidence counting",
                "trend calculation",
                "persistence calculation",
                "alert threshold decision",
                "historical anti-leakage checks",
            ],
        },
    }
