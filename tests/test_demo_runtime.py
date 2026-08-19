from __future__ import annotations

from datetime import date

import numpy as np
from fastapi.testclient import TestClient

from recallzero.api.app import create_app
from recallzero.config import Settings
from recallzero.data import FileRepository
from recallzero.demo_runtime import DEFAULT_DEMO_ODI, demo_readiness, run_fresh_nvidia_trace
from recallzero.intelligence.embeddings import EmbeddingResult
from recallzero.models import Complaint, EmbeddingMethod, ExtractionMethod, FailureSignature, Vehicle


def _complaint() -> Complaint:
    vehicle = Vehicle(make="FORD", model="MUSTANG MACH-E", model_years=(2021, 2022))
    return Complaint(
        odi_number=DEFAULT_DEMO_ODI,
        vehicle=vehicle,
        received_date=date(2022, 5, 25),
        components=("ELECTRICAL SYSTEM",),
        narrative="Stop Safely Now appeared while driving and the vehicle lost propulsion.",
    )


def test_demo_readiness_reports_cached_complaint_and_services(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        use_nim=True,
        nim_base_url="http://127.0.0.1:8000/v1",
        embedding_base_url="http://127.0.0.1:8001/v1",
    )
    repository = FileRepository(tmp_path)
    complaint = _complaint()
    repository.save_complaints(complaint.vehicle, [complaint])

    payload = demo_readiness(settings, repository)

    assert payload["ready_for_live_trace"] is True
    assert payload["checks"]["demo_complaint_cached"] is True
    assert payload["services"]["extraction"]["endpoint_kind"] == "Local NVIDIA NIM"
    assert payload["services"]["risk_engine"]["llm_decision"] is False


async def test_fresh_trace_uses_direct_nvidia_paths_without_cache_write(tmp_path, monkeypatch) -> None:
    settings = Settings(
        data_dir=tmp_path,
        use_nim=True,
        nim_base_url="http://127.0.0.1:8000/v1",
        embedding_base_url="http://127.0.0.1:8001/v1",
    )
    repository = FileRepository(tmp_path)
    complaint = _complaint()
    repository.save_complaints(complaint.vehicle, [complaint])

    async def fake_extract(self, item):  # noqa: ANN001
        assert item.odi_number == DEFAULT_DEMO_ODI
        return FailureSignature(
            complaint_id=item.odi_number,
            system="ELECTRICAL SYSTEM",
            failure_mode="LOSS OF MOTIVE POWER",
            consequence="VEHICLE DISABLED",
            severity_indicators=("loss_of_motive_power", "vehicle_in_motion"),
            confidence=0.95,
            extraction_method=ExtractionMethod.NIM,
            model_name=settings.llm_model,
        )

    async def fake_embed(self, texts):  # noqa: ANN001
        assert len(texts) == 1
        return EmbeddingResult(
            matrix=np.asarray([[0.1, 0.2, 0.3, 0.4]], dtype=np.float32),
            method=EmbeddingMethod.NIM,
            model_name=settings.embedding_model,
        )

    monkeypatch.setattr("recallzero.demo_runtime.NIMFailureExtractor.extract", fake_extract)
    monkeypatch.setattr("recallzero.demo_runtime.NIMEmbedder.embed", fake_embed)

    payload = await run_fresh_nvidia_trace(
        settings=settings,
        repository=repository,
        odi_number=DEFAULT_DEMO_ODI,
    )

    assert payload["fresh_inference"] is True
    assert payload["cache_write"] is False
    assert payload["detector_risk_recomputed"] is False
    assert payload["extraction"]["signature"]["failure_mode"] == "LOSS OF MOTIVE POWER"
    assert payload["embedding"]["dimension"] == 4
    assert payload["embedding"]["preview"] == [0.1, 0.2, 0.3, 0.4]


def test_live_trace_route_is_separate_from_detector(tmp_path, monkeypatch) -> None:
    async def fake_trace(*, settings, repository, odi_number):  # noqa: ANN001
        return {
            "fresh_inference": True,
            "cache_write": False,
            "detector_risk_recomputed": False,
            "complaint": {"odi_number": odi_number},
            "extraction": {"model": settings.llm_model},
            "embedding": {"model": settings.embedding_model},
            "decision_boundary": {"ai_used_for": [], "ai_not_used_for": []},
        }

    monkeypatch.setattr("recallzero.api.routes.run_fresh_nvidia_trace", fake_trace)
    client = TestClient(create_app(Settings(data_dir=tmp_path, use_nim=False)))
    response = client.post("/api/v1/demo/nvidia-trace", json={"odi_number": "11466150"})

    assert response.status_code == 200
    assert response.json()["fresh_inference"] is True
    assert response.json()["detector_risk_recomputed"] is False
