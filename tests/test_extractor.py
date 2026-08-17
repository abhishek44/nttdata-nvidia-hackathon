import pytest

from recallzero.intelligence.extractor import HeuristicFailureExtractor
from recallzero.models import Complaint, Vehicle


@pytest.mark.asyncio
async def test_heuristic_extracts_loss_of_motive_power() -> None:
    complaint = Complaint(
        odi_number="1",
        vehicle=Vehicle(make="Demo", model="EV", model_years=(2022,)),
        received_date="2022-05-10",
        components=("POWER TRAIN",),
        narrative="The vehicle suddenly lost motive power while driving on the highway and coasted to a stop.",
    )
    signature = await HeuristicFailureExtractor().extract(complaint)
    assert signature.system == "POWER TRAIN"
    assert signature.failure_mode == "LOSS OF MOTIVE POWER"
    assert "loss_of_motive_power" in signature.severity_indicators
    assert "vehicle_in_motion" in signature.severity_indicators


@pytest.mark.asyncio
async def test_heuristic_selects_supported_component_from_multiple_components() -> None:
    complaint = Complaint(
        odi_number="2",
        vehicle=Vehicle(make="Demo", model="EV", model_years=(2022,)),
        received_date="2022-05-11",
        components=("ELECTRICAL SYSTEM", "POWER TRAIN"),
        narrative="The vehicle lost motive power while driving and coasted to the shoulder.",
    )
    signature = await HeuristicFailureExtractor().extract(complaint)
    assert signature.system == "POWER TRAIN"


@pytest.mark.asyncio
async def test_nim_mode_refreshes_heuristic_cache() -> None:
    from recallzero.intelligence.extractor import HybridFailureExtractor
    from recallzero.models import ExtractionMethod, FailureSignature

    complaint = Complaint(
        odi_number="3",
        vehicle=Vehicle(make="Demo", model="EV", model_years=(2022,)),
        received_date="2022-05-12",
        components=("POWER TRAIN",),
        narrative="The vehicle lost motive power while driving.",
    )
    cached = await HeuristicFailureExtractor().extract(complaint)

    class StubNIMExtractor:
        model_name = "test-model"

        def __init__(self) -> None:
            self.calls = 0

        async def extract(self, item: Complaint) -> FailureSignature:
            self.calls += 1
            return FailureSignature(
                complaint_id=item.odi_number,
                system="POWER TRAIN",
                failure_mode="LOSS OF MOTIVE POWER",
                extraction_method=ExtractionMethod.NIM,
                model_name=self.model_name,
            )

    stub = StubNIMExtractor()
    hybrid = HybridFailureExtractor(heuristic=HeuristicFailureExtractor(), nim=stub)  # type: ignore[arg-type]
    result = await hybrid.extract_many([complaint], cached={complaint.odi_number: cached})
    assert stub.calls == 1
    assert result[0].extraction_method == ExtractionMethod.NIM
