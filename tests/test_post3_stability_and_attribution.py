import json
import logging
from pathlib import Path

import numpy as np
import pytest

from recallzero.benchmark import (
    BenchmarkCandidateRecord,
    BenchmarkCaseResult,
    BenchmarkRunResult,
    BenchmarkSnapshotRecord,
)
from recallzero.benchmark_attribution import run_attribution_experiment
from recallzero.config import Settings
from recallzero.data.repository import FileRepository
from recallzero.intelligence.embeddings import EmbeddingResult
from recallzero.intelligence.extractor import NIMFailureExtractor, NIM_FAILURE_SIGNATURE_SCHEMA
from recallzero.models import (
    Complaint,
    EmbeddingMethod,
    ExtractionMethod,
    FailureSignature,
    Recall,
    Vehicle,
)
from recallzero.recall.target_attributor import TargetAttributor
from recallzero.utils import stable_id


def _wrangler_complaint() -> Complaint:
    return Complaint(
        odi_number="11130246",
        vehicle=Vehicle(make="JEEP", model="WRANGLER", model_years=(2018,)),
        received_date="2018-08-01",
        components=("AIR BAGS",),
        narrative="The airbag deployed during the reported incident while the vehicle was moving.",
    )


@pytest.mark.asyncio
async def test_post3_unknown_auxiliary_severity_label_is_nonfatal_and_dropped(caplog) -> None:
    class StubClient:
        def __init__(self) -> None:
            self.calls = 0

        async def chat_completion(self, **_kwargs):
            self.calls += 1
            return json.dumps(
                {
                    "system": "AIR BAGS",
                    "subsystem": "FRONTAL AIR BAG",
                    "failure_mode": "UNEXPECTED AIRBAG DEPLOYMENT",
                    "symptom": "Airbag deployed",
                    "operating_state": "VEHICLE IN MOTION",
                    "consequence": None,
                    "severity_indicators": ["airbag_deployed", "vehicle_in_motion"],
                    "confidence": 0.93,
                }
            )

    client = StubClient()
    caplog.set_level(logging.WARNING)
    signature = await NIMFailureExtractor(client, "test-model").extract(_wrangler_complaint())  # type: ignore[arg-type]

    assert client.calls == 1  # auxiliary vocabulary drift does not waste a repair attempt
    assert signature.extraction_method == ExtractionMethod.NIM
    assert signature.severity_indicators == ("vehicle_in_motion",)
    assert "airbag_deployed" not in signature.canonical_text()
    assert "unsupported auxiliary severity indicator" in caplog.text
    assert "airbag_deployed" in caplog.text


def test_post3_guided_schema_does_not_enum_auxiliary_severity_labels() -> None:
    items = NIM_FAILURE_SIGNATURE_SCHEMA["properties"]["severity_indicators"]["items"]
    assert items["type"] == "string"
    assert "enum" not in items


def test_embedding_substitution_changes_only_ten_percent_semantic_term() -> None:
    baseline = {
        "explicit_campaign_reference": 0.0,
        "failure_mechanism": 0.0,
        "consequence_family": 0.0,
        "component": 1.0,
        "component_compatible": 1.0,
        "subsystem": 0.05,
        "consequence": 0.0385,
        "semantic_lexical": 0.043,
        "final": 0.2131,
    }
    experimental = TargetAttributor.substitute_embedding(baseline, 0.80)
    # 0.20 component + 0.005 subsystem + 0.00385 consequence + 0.08 semantic
    assert experimental["final_embedding_experiment"] == pytest.approx(0.2889, abs=1e-4)
    assert experimental["semantic_embedding"] == 0.8
    assert experimental["component"] == baseline["component"]
    assert experimental["failure_mechanism"] == baseline["failure_mechanism"]


def test_embedding_substitution_preserves_component_gate_cap() -> None:
    baseline = {
        "explicit_campaign_reference": 0.0,
        "failure_mechanism": 1.0,
        "consequence_family": 1.0,
        "component": 0.0,
        "component_compatible": 0.0,
        "subsystem": 1.0,
        "consequence": 1.0,
        "semantic_lexical": 0.0,
        "final": 0.44,
    }
    experimental = TargetAttributor.substitute_embedding(baseline, 1.0)
    assert experimental["final_embedding_experiment"] == 0.44


@pytest.mark.asyncio
async def test_offline_attribution_experiment_uses_cache_and_not_detector(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    settings = Settings(
        data_dir=data_dir,
        use_nim=True,
        nvidia_api_key="test-key",
        embedding_base_url="https://embedding.invalid/v1",
        embedding_model="test-embed-model",
    )
    repository = FileRepository(data_dir)
    vehicle = Vehicle(make="TEST", model="CAR", model_years=(2020,))
    complaint = Complaint(
        odi_number="1",
        vehicle=vehicle,
        received_date="2020-06-01",
        components=("ENGINE",),
        narrative="Engine stalled while driving.",
    )
    signature = FailureSignature(
        complaint_id="1",
        system="ENGINE",
        subsystem="ENGINE",
        failure_mode="ENGINE STALL",
        symptom="Engine stalled",
        operating_state="VEHICLE IN MOTION",
        consequence="Loss of motive power",
        severity_indicators=("loss_of_motive_power", "vehicle_in_motion"),
        extraction_method=ExtractionMethod.NIM,
        model_name="test-llm",
    )
    recall = Recall(
        campaign_number="20V999000",
        vehicle=vehicle,
        report_received_date="2020-12-31",
        component="ENGINE",
        summary="Engine damage may result in an engine stall.",
        consequence="An engine stall can increase crash risk.",
    )
    repository.save_complaints(vehicle, [complaint])
    repository.save_signatures(vehicle, [signature])
    repository.save_recalls(vehicle, [recall])

    candidate = BenchmarkCandidateRecord(
        signal_id="sig-1",
        lineage_id="lin-1",
        signal_scope="cluster",
        issue="ENGINE — ENGINE STALL",
        cluster_id="cl-1",
        system="ENGINE",
        failure_mode="ENGINE STALL",
        defect_family="LOSS_OF_MOTIVE_POWER",
        failure_mechanism="GENERAL_POWERTRAIN",
        consequence_family="LOSS_OF_MOTIVE_POWER",
        source_systems=("ENGINE",),
        member_ids=("1",),
        embedding_method="nim",
        evidence_count=1,
        risk_score=80.0,
        alert=True,
        distance_to_alert_threshold=0.0,
        threshold_margin=5.0,
        posthoc_target_score=0.5324,
        posthoc_target_breakdown={"final": 0.5324},
    )
    snapshot = BenchmarkSnapshotRecord(
        cutoff_date="2020-06-02",
        complaint_count_visible=1,
        signal_count=1,
        max_risk_score=80.0,
        max_risk_signal_id="sig-1",
        distance_to_alert_threshold=0.0,
        threshold_margin=5.0,
        alert_signal_ids=("sig-1",),
        alert_lineage_ids=("lin-1",),
        top_candidates=(candidate,),
    )
    case_id = stable_id("case", "positive", vehicle.slug, "20V999000", "2020-12-31")
    case = BenchmarkCaseResult(
        case_id=case_id,
        name="Synthetic unmatched positive",
        expected_role="positive",
        benchmark_split="validation",
        vehicle=vehicle,
        campaign_number="20V999000",
        boundary_date="2020-12-31",
        status="EARLY_ALERT_TARGET_UNMATCHED",
        benchmark_valid=True,
        snapshots=(snapshot,),
    )
    raw = BenchmarkRunResult(
        benchmark_run_id="bench-test",
        benchmark_id="bench",
        benchmark_split="validation",
        freeze_id="recallzero-detector-v1",
        freeze_verified=True,
        lock_verified=True,
        manifest_path="config/candidates.yml",
        manifest_sha256="x",
        lock_path="lock.json",
        freeze_manifest_path="freeze.yml",
        case_count=1,
        cases=(case,),
        aggregate={},
        acceptance_criteria={},
    )
    raw_path = tmp_path / "raw.json"
    raw_path.write_text(raw.model_dump_json(), encoding="utf-8")
    manifest_path = tmp_path / "candidates.yml"
    manifest_path.write_text(
        """benchmark:\n  id: bench\n  preregistered: true\ncandidates:\n  - name: Synthetic unmatched positive\n    make: TEST\n    model: CAR\n    model_years: [2020]\n    campaign_number: 20V999000\n    official_recall_date: 2020-12-31\n    expected_role: positive\n    benchmark_split: validation\n    enabled: true\n""",
        encoding="utf-8",
    )

    class FakeClient:
        configured = True

        def __init__(self, **_kwargs):
            pass

    class FakeEmbedder:
        def __init__(self, _client, model_name, batch_size=64):
            self.model_name = model_name
            self.batch_size = batch_size

        async def embed(self, texts):
            # Give every unique text the same direction: this is intentionally a
            # score-inflation scenario that verifies the offline path, not quality.
            return EmbeddingResult(
                matrix=np.asarray([[1.0, 0.0] for _ in texts], dtype=np.float32),
                method=EmbeddingMethod.NIM,
                model_name=self.model_name,
            )

    monkeypatch.setattr("recallzero.benchmark_attribution.NIMClient", FakeClient)
    monkeypatch.setattr("recallzero.benchmark_attribution.NIMEmbedder", FakeEmbedder)

    result = await run_attribution_experiment(
        settings=settings,
        raw_result_path=raw_path,
        candidates_path=manifest_path,
        target_match_threshold=0.45,
        top_target_count=10,
    )
    assert result.detector_recomputed is False
    assert result.llm_calls_required == 0
    assert result.case_count == 1
    assert result.candidate_count == 1
    assert result.unique_text_count == 2
    assert result.estimated_embedding_api_calls == 1
    row = result.cases[0].candidates[0]
    assert row.alert is True
    assert row.embedding_similarity == 1.0
    assert row.experimental_target_score >= row.baseline_target_score
