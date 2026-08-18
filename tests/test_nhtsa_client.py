import httpx
import pytest

from recallzero.data import NHTSAClient, NHTSAError, NHTSAHTTPError
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


def _mock_complaint(odi: int, *, model: str, filed: str = "01/02/2019") -> dict[str, object]:
    return {
        "odiNumber": odi,
        "dateComplaintFiled": filed,
        "components": "SERVICE BRAKES",
        "summary": f"Mock complaint {odi} for {model}.",
        "crash": False,
        "fire": False,
        "numberOfInjuries": 0,
        "numberOfDeaths": 0,
        "products": [
            {
                "type": "Vehicle",
                "productYear": filed[-4:],
                "productMake": "FORD" if "F-150" in model else "TESLA",
                "productModel": model,
            }
        ],
    }


@pytest.mark.asyncio
async def test_complaint_lookup_aggregates_catalog_family_after_base_400() -> None:
    attempted_models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/products/vehicle/models":
            assert request.url.params["issueType"] == "c"
            return httpx.Response(
                200,
                json={
                    "count": 4,
                    "message": "Results returned successfully",
                    "results": [
                        {"model": "MODEL Y"},
                        {"model": "MODEL Y 5-SEAT"},
                        {"model": "MODEL Y 7-SEAT"},
                        {"model": "MODEL S"},
                    ],
                },
            )
        if request.url.path == "/complaints/complaintsByVehicle":
            model = request.url.params["model"]
            attempted_models.append(model)
            if model == "MODEL Y":
                return httpx.Response(
                    400,
                    json={"count": 0, "message": "Results returned successfully", "results": []},
                )
            if model == "MODEL Y 5-SEAT":
                return httpx.Response(
                    200,
                    json={
                        "count": 1,
                        "message": "Results returned successfully",
                        "results": [_mock_complaint(1101, model=model, filed="01/02/2021")],
                    },
                )
            if model == "MODEL Y 7-SEAT":
                return httpx.Response(
                    200,
                    json={
                        "count": 2,
                        "message": "Results returned successfully",
                        "results": [
                            _mock_complaint(1101, model=model, filed="01/02/2021"),
                            _mock_complaint(1102, model=model, filed="02/03/2021"),
                        ],
                    },
                )
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(base_url="https://api.nhtsa.gov", transport=httpx.MockTransport(handler)) as client:
        complaints, raw = await NHTSAClient(client=client, max_retries=0).fetch_complaints(
            Vehicle(make="TESLA", model="MODEL Y", model_years=(2021,))
        )

    assert [item.odi_number for item in complaints] == ["1101", "1102"]
    query = raw["2021"]["recallzeroQuery"]
    assert query["adapterRevision"] == "nhtsa-complaint-catalog-v2"
    assert query["requestedModel"] == "MODEL Y"
    assert query["catalogModelsResolved"] == [
        "MODEL Y",
        "MODEL Y 5-SEAT",
        "MODEL Y 7-SEAT",
    ]
    assert query["rejectedModelsHttp400"] == ["MODEL Y"]
    assert query["deduplicatedComplaintCount"] == 2
    assert set(attempted_models) == {
        "MODEL Y 5-SEAT",
        "MODEL Y 7-SEAT",
        "MODEL Y",
    }


@pytest.mark.asyncio
async def test_complaint_lookup_expands_f150_body_variants_and_deduplicates() -> None:
    attempted_models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/products/vehicle/models":
            return httpx.Response(
                200,
                json={
                    "count": 4,
                    "message": "Results returned successfully",
                    "results": [
                        {"model": "F-150"},
                        {"model": "F-150 SUPERCAB"},
                        {"model": "F-150 SUPERCREW"},
                        {"model": "F-250"},
                    ],
                },
            )
        if request.url.path == "/complaints/complaintsByVehicle":
            model = request.url.params["model"]
            attempted_models.append(model)
            if model == "F-150":
                results: list[dict[str, object]] = []
            elif model == "F-150 SUPERCAB":
                results = [
                    _mock_complaint(2101, model=model, filed="01/02/2016"),
                    _mock_complaint(2102, model=model, filed="02/03/2016"),
                ]
            elif model == "F-150 SUPERCREW":
                results = [
                    _mock_complaint(2102, model=model, filed="02/03/2016"),
                    _mock_complaint(2103, model=model, filed="03/04/2016"),
                ]
            else:
                raise AssertionError(f"Unrelated catalog model was queried: {model}")
            return httpx.Response(
                200,
                json={"count": len(results), "message": "Results returned successfully", "results": results},
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(base_url="https://api.nhtsa.gov", transport=httpx.MockTransport(handler)) as client:
        complaints, raw = await NHTSAClient(client=client).fetch_complaints(
            Vehicle(make="FORD", model="F-150", model_years=(2016,))
        )

    assert [item.odi_number for item in complaints] == ["2101", "2102", "2103"]
    assert attempted_models == ["F-150", "F-150 SUPERCAB", "F-150 SUPERCREW"]
    assert raw["2016"]["recallzeroQuery"]["catalogModelsResolved"] == [
        "F-150",
        "F-150 SUPERCAB",
        "F-150 SUPERCREW",
    ]


@pytest.mark.asyncio
async def test_complaint_family_matching_does_not_use_substring_or_numeric_prefix() -> None:
    nhtsa = NHTSAClient()
    assert nhtsa._is_catalog_family_match("500", "500 2-DOOR") is True
    assert nhtsa._is_catalog_family_match("500", "500X") is False
    assert nhtsa._is_catalog_family_match("MODEL Y", "MODEL Y 7-SEAT") is True
    assert nhtsa._is_catalog_family_match("MODEL Y", "MODEL S") is False
    assert nhtsa._is_catalog_family_match("F-150", "F-150 SUPERCREW") is True
    assert nhtsa._is_catalog_family_match("F-150", "F-250") is False


@pytest.mark.asyncio
async def test_complaint_lookup_all_resolved_variants_400_is_hard_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/products/vehicle/models":
            return httpx.Response(
                200,
                json={"count": 1, "message": "Results returned successfully", "results": [{"model": "MODEL Y 7-SEAT"}]},
            )
        if request.url.path == "/complaints/complaintsByVehicle":
            return httpx.Response(400, json={"count": 0, "message": "Results returned successfully", "results": []})
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(base_url="https://api.nhtsa.gov", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(NHTSAError, match="rejected all resolved model variants"):
            await NHTSAClient(client=client, max_retries=0).fetch_complaints(
                Vehicle(make="TESLA", model="MODEL Y", model_years=(2021,))
            )


@pytest.mark.asyncio
async def test_complaint_lookup_valid_200_empty_is_valid_empty_population() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/products/vehicle/models":
            return httpx.Response(
                200,
                json={"count": 1, "message": "Results returned successfully", "results": [{"model": "CAMRY"}]},
            )
        if request.url.path == "/complaints/complaintsByVehicle":
            return httpx.Response(200, json={"count": 0, "message": "Results returned successfully", "results": []})
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(base_url="https://api.nhtsa.gov", transport=httpx.MockTransport(handler)) as client:
        complaints, raw = await NHTSAClient(client=client).fetch_complaints(
            Vehicle(make="TOYOTA", model="CAMRY", model_years=(2022,))
        )

    assert complaints == []
    assert raw["2022"]["recallzeroQuery"]["successfulModels"] == ["CAMRY"]
    assert raw["2022"]["recallzeroQuery"]["deduplicatedComplaintCount"] == 0


@pytest.mark.asyncio
async def test_complaint_lookup_resolves_mach_e_punctuation_alias_without_family_drift() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/products/vehicle/models":
            return httpx.Response(
                200,
                json={
                    "count": 3,
                    "message": "Results returned successfully",
                    "results": [
                        {"model": "MUSTANG MACH E"},
                        {"model": "MUSTANG"},
                        {"model": "MUSTANG MACH 1"},
                    ],
                },
            )
        if request.url.path == "/complaints/complaintsByVehicle":
            model = request.url.params["model"]
            seen.append(model)
            assert model == "MUSTANG MACH E"
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "message": "Results returned successfully",
                    "results": [
                        {
                            "odiNumber": 3101,
                            "dateComplaintFiled": "01/02/2021",
                            "components": "ELECTRICAL SYSTEM",
                            "summary": "Vehicle lost propulsion while driving.",
                            "crash": False,
                            "fire": False,
                            "numberOfInjuries": 0,
                            "numberOfDeaths": 0,
                            "products": [
                                {
                                    "type": "Vehicle",
                                    "productYear": "2021",
                                    "productMake": "FORD",
                                    "productModel": "MUSTANG MACH E",
                                }
                            ],
                        }
                    ],
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(base_url="https://api.nhtsa.gov", transport=httpx.MockTransport(handler)) as client:
        complaints, raw = await NHTSAClient(client=client).fetch_complaints(
            Vehicle(make="FORD", model="MUSTANG MACH-E", model_years=(2021,))
        )

    assert len(complaints) == 1
    assert seen == ["MUSTANG MACH E"]
    assert raw["2021"]["recallzeroQuery"]["catalogModelsResolved"] == ["MUSTANG MACH E"]
