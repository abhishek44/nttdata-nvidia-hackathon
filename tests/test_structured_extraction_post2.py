import json

import pytest

from recallzero.benchmark import _enforce_strict_nim_validation
from recallzero.intelligence.extractor import (
    HeuristicFailureExtractor,
    HybridFailureExtractor,
    NIMFailureExtractor,
    NIMStructuredExtractionError,
    NIM_FAILURE_SIGNATURE_SCHEMA,
)
from recallzero.models import Complaint, ExtractionMethod, Vehicle


OBSERVED_NULL_FAILURE_MODE_ODIS = (
    "11443132",
    "11443932",
    "11448380",
    "11459714",
    "11466251",
)


def _complaint(odi: str) -> Complaint:
    return Complaint(
        odi_number=odi,
        vehicle=Vehicle(make="FORD", model="MUSTANG MACH-E", model_years=(2021, 2022)),
        received_date="2022-05-01",
        components=("VISIBILITY",),
        narrative="Owner reports a vehicle safety concern and requests repair assistance.",
    )


def _payload(*, failure_mode):
    return json.dumps(
        {
            "system": "VISIBILITY",
            "subsystem": None,
            "failure_mode": failure_mode,
            "symptom": None,
            "operating_state": None,
            "consequence": None,
            "severity_indicators": [],
            "confidence": 0.9,
        }
    )


def test_guided_schema_marks_core_strings_nonempty() -> None:
    properties = NIM_FAILURE_SIGNATURE_SCHEMA["properties"]
    assert properties["system"]["type"] == "string"
    assert properties["system"]["minLength"] == 1
    assert properties["failure_mode"]["type"] == "string"
    assert properties["failure_mode"]["minLength"] == 1
    assert "failure_mode" in NIM_FAILURE_SIGNATURE_SCHEMA["required"]


@pytest.mark.asyncio
@pytest.mark.parametrize("odi", OBSERVED_NULL_FAILURE_MODE_ODIS)
async def test_observed_null_failure_mode_is_repaired_by_nim(odi: str) -> None:
    class StubClient:
        def __init__(self) -> None:
            self.calls = []

        async def chat_completion(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return _payload(failure_mode=None)
            return _payload(failure_mode="UNSPECIFIED FAILURE")

    client = StubClient()
    signature = await NIMFailureExtractor(client, "test-model").extract(_complaint(odi))  # type: ignore[arg-type]

    assert len(client.calls) == 2
    assert signature.extraction_method == ExtractionMethod.NIM
    assert signature.failure_mode == "UNSPECIFIED FAILURE"
    repair_messages = client.calls[1]["messages"]
    assert "failure_mode`` MUST NOT be null" in repair_messages[0]["content"]
    assert "validation_error" in repair_messages[1]["content"]
    assert client.calls[1]["guided_json"]["properties"]["failure_mode"]["minLength"] == 1


@pytest.mark.asyncio
async def test_invalid_repair_fails_closed_with_structured_nim_error() -> None:
    class StubClient:
        async def chat_completion(self, **_kwargs):
            return _payload(failure_mode=None)

    with pytest.raises(NIMStructuredExtractionError, match="after one repair attempt"):
        await NIMFailureExtractor(StubClient(), "test-model").extract(_complaint("repair-fails"))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_benchmark_strict_mode_never_uses_heuristic_after_structured_failure() -> None:
    class FailingNIM:
        model_name = "test-model"

        async def extract(self, _complaint):
            raise NIMStructuredExtractionError("structured extraction invalid")

    class CountingHeuristic(HeuristicFailureExtractor):
        def __init__(self) -> None:
            self.calls = 0

        async def extract(self, complaint):
            self.calls += 1
            return await super().extract(complaint)

    heuristic = CountingHeuristic()
    hybrid = HybridFailureExtractor(
        heuristic=heuristic,
        nim=FailingNIM(),  # type: ignore[arg-type]
        fallback_on_error=True,
        fallback_on_transient_error=True,
    )

    class Pipeline:
        extractor = hybrid

    _enforce_strict_nim_validation(Pipeline(), 1.0)  # type: ignore[arg-type]
    assert hybrid.fallback_on_error is False
    assert hybrid.fallback_on_transient_error is False

    with pytest.raises(NIMStructuredExtractionError):
        await hybrid.extract(_complaint("strict-1"))
    assert heuristic.calls == 0


@pytest.mark.asyncio
async def test_non_strict_hybrid_still_preserves_interactive_heuristic_fallback() -> None:
    class FailingNIM:
        model_name = "test-model"

        async def extract(self, _complaint):
            raise NIMStructuredExtractionError("structured extraction invalid")

    hybrid = HybridFailureExtractor(
        heuristic=HeuristicFailureExtractor(),
        nim=FailingNIM(),  # type: ignore[arg-type]
        fallback_on_error=True,
    )
    signature = await hybrid.extract(_complaint("interactive-1"))
    assert signature.extraction_method == ExtractionMethod.HEURISTIC


def test_non_strict_benchmark_policy_does_not_force_fail_closed() -> None:
    class Extractor:
        fallback_on_error = True
        fallback_on_transient_error = True

    class Pipeline:
        extractor = Extractor()

    pipeline = Pipeline()
    _enforce_strict_nim_validation(pipeline, 0.95)  # type: ignore[arg-type]
    assert pipeline.extractor.fallback_on_error is True
    assert pipeline.extractor.fallback_on_transient_error is True
