from datetime import date

from recallzero.data.normalizer import normalize_complaint, normalize_recall
from recallzero.models import Vehicle


def test_normalize_nhtsa_complaint_payload() -> None:
    vehicle = Vehicle(make="Ford", model="Mustang Mach-E", model_years=(2021,))
    payload = {
        "odiNumber": 12345678,
        "manufacturer": "Ford Motor Company",
        "crash": True,
        "fire": False,
        "numberOfInjuries": 1,
        "numberOfDeaths": 0,
        "dateOfIncident": "05/01/2022",
        "dateComplaintFiled": "05/04/2022",
        "vin": "3FMTK3SU1MM123456",
        "components": "ELECTRICAL SYSTEM,POWER TRAIN",
        "summary": "Vehicle lost motive power while driving.",
        "products": [
            {
                "type": "Vehicle",
                "productYear": "2021",
                "productMake": "FORD",
                "productModel": "MUSTANG MACH-E",
            }
        ],
    }
    complaint = normalize_complaint(payload, vehicle)
    assert complaint.odi_number == "12345678"
    assert complaint.received_date == date(2022, 5, 4)
    assert complaint.crash is True
    assert complaint.injuries == 1
    assert complaint.components == ("ELECTRICAL SYSTEM", "POWER TRAIN")
    assert complaint.raw_payload["odiNumber"] == 12345678


def test_normalize_recall_campaign() -> None:
    vehicle = Vehicle(make="Ford", model="Mustang Mach-E", model_years=(2021,))
    recall = normalize_recall(
        {
            "NHTSACampaignNumber": "22V-412000",
            "ReportReceivedDate": "10/06/2022",
            "Component": "ELECTRICAL SYSTEM:PROPULSION",
            "Summary": "A high voltage contactor may overheat and cause loss of motive power.",
            "ModelYear": "2021",
            "Make": "FORD",
            "Model": "MUSTANG MACH-E",
        },
        vehicle,
    )
    assert recall.campaign_number == "22V412000"
    assert recall.report_received_date == date(2022, 6, 10)


def test_recall_and_complaint_slash_dates_use_endpoint_specific_order() -> None:
    vehicle = Vehicle(make="Ford", model="Mustang Mach-E", model_years=(2021,))
    complaint = normalize_complaint(
        {
            "odiNumber": 999,
            "dateComplaintFiled": "01/07/2022",
            "summary": "Example complaint.",
            "products": [{"productYear": "2021", "productMake": "FORD", "productModel": "MUSTANG MACH-E"}],
        },
        vehicle,
    )
    recall = normalize_recall(
        {
            "NHTSACampaignNumber": "22V412000",
            "ReportReceivedDate": "10/06/2022",
            "ModelYear": "2021",
            "Make": "FORD",
            "Model": "MUSTANG MACH E",
        },
        vehicle,
    )
    assert complaint.received_date == date(2022, 1, 7)
    assert recall.report_received_date == date(2022, 6, 10)
