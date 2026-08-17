import httpx
import pytest

from recallzero.data import NHTSAClient, NHTSAHTTPError
from recallzero.models import Vehicle


@pytest.mark.asyncio
async def test_nhtsa_client_parses_mock_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "complaintsByVehicle" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "message": "Results returned successfully",
                    "results": [
                        {
                            "odiNumber": 1,
                            "dateComplaintFiled": "01/02/2022",
                            "components": "STEERING",
                            "summary": "Power steering stopped while driving.",
                            "crash": False,
                            "fire": False,
                            "numberOfInjuries": 0,
                            "numberOfDeaths": 0,
                        }
                    ],
                },
            )
        return httpx.Response(200, json={"Count": 0, "Message": "Results returned successfully", "results": []})

    async with httpx.AsyncClient(base_url="https://api.nhtsa.gov", transport=httpx.MockTransport(handler)) as client:
        nhtsa = NHTSAClient(client=client)
        complaints, _ = await nhtsa.fetch_complaints(Vehicle(make="Demo", model="Car", model_years=(2022,)))
    assert len(complaints) == 1
    assert complaints[0].components == ("STEERING",)


@pytest.mark.asyncio
async def test_campaign_lookup_uses_campaign_number_query() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "Count": 1,
                "Message": "Results returned successfully",
                "results": [
                    {
                        "NHTSACampaignNumber": "22V412000",
                        "ReportReceivedDate": "10/06/2022",
                        "Component": "ELECTRICAL SYSTEM:PROPULSION SYSTEM",
                        "Summary": "High-voltage battery contactors may overheat and open while driving.",
                        "Consequence": "A loss of motive power can increase the risk of a crash.",
                    }
                ],
            },
        )

    async with httpx.AsyncClient(base_url="https://api.nhtsa.gov", transport=httpx.MockTransport(handler)) as client:
        result = await NHTSAClient(client=client).fetch_campaign("22-V412000")
    assert result is not None
    assert result.campaign_number == "22V412000"
    assert seen[0].url.path == "/recalls/campaignNumber"
    assert seen[0].url.params["campaignNumber"] == "22V412000"


@pytest.mark.asyncio
async def test_recall_lookup_resolves_catalog_punctuation_alias() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/products/vehicle/models":
            assert request.url.params["issueType"] == "r"
            return httpx.Response(
                200,
                json={
                    "count": 2,
                    "message": "Results returned successfully",
                    "results": [
                        {"modelYear": "2021", "make": "FORD", "model": "MUSTANG MACH E"},
                        {"modelYear": "2021", "make": "FORD", "model": "MUSTANG"},
                    ],
                },
            )
        if request.url.path == "/recalls/recallsByVehicle":
            assert request.url.params["model"] == "MUSTANG MACH E"
            return httpx.Response(
                200,
                json={
                    "Count": 1,
                    "Message": "Results returned successfully",
                    "results": [
                        {
                            "NHTSACampaignNumber": "22V412000",
                            "ReportReceivedDate": "10/06/2022",
                            "Component": "ELECTRICAL SYSTEM:PROPULSION SYSTEM",
                            "Summary": "High-voltage battery contactors may overheat.",
                            "Consequence": "A loss of motive power can increase crash risk.",
                            "ModelYear": "2021",
                            "Make": "FORD",
                            "Model": "MUSTANG MACH E",
                        }
                    ],
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(base_url="https://api.nhtsa.gov", transport=httpx.MockTransport(handler)) as client:
        recalls, raw = await NHTSAClient(client=client).fetch_recalls(
            Vehicle(make="FORD", model="MUSTANG MACH-E", model_years=(2021,))
        )

    assert len(recalls) == 1
    assert recalls[0].campaign_number == "22V412000"
    assert raw["2021"]["recallzeroQuery"]["requestedModel"] == "MUSTANG MACH-E"
    assert raw["2021"]["recallzeroQuery"]["resolvedModel"] == "MUSTANG MACH E"
    assert [request.url.path for request in seen] == [
        "/products/vehicle/models",
        "/recalls/recallsByVehicle",
    ]


@pytest.mark.asyncio
async def test_non_retryable_400_is_attempted_once() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"message": "invalid exact model"})

    async with httpx.AsyncClient(base_url="https://api.nhtsa.gov", transport=httpx.MockTransport(handler)) as client:
        nhtsa = NHTSAClient(client=client, max_retries=5)
        with pytest.raises(NHTSAHTTPError, match="HTTP 400"):
            await nhtsa._get_json("/recalls/recallsByVehicle", params={"model": "BAD"})

    assert calls == 1


@pytest.mark.asyncio
async def test_recall_lookup_falls_back_to_safe_space_variant_when_catalog_unavailable() -> None:
    attempted_models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/products/vehicle/models":
            return httpx.Response(503, json={"message": "temporary catalog outage"})
        if request.url.path == "/recalls/recallsByVehicle":
            model = request.url.params["model"]
            attempted_models.append(model)
            if model == "MUSTANG MACH-E":
                return httpx.Response(400, json={"message": "invalid model"})
            if model == "MUSTANG MACH E":
                return httpx.Response(200, json={"Count": 0, "Message": "Results returned successfully", "results": []})
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(base_url="https://api.nhtsa.gov", transport=httpx.MockTransport(handler)) as client:
        recalls, _ = await NHTSAClient(client=client, max_retries=0).fetch_recalls(
            Vehicle(make="FORD", model="MUSTANG MACH-E", model_years=(2021,))
        )

    assert recalls == []
    assert attempted_models == ["MUSTANG MACH-E", "MUSTANG MACH E"]
