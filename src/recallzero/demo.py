from __future__ import annotations

from datetime import date, timedelta

from recallzero.models import Complaint, Recall, Vehicle


def build_demo_records() -> tuple[Vehicle, list[Complaint], list[Recall], Recall, date]:
    vehicle = Vehicle(make="DEMO MOTORS", model="VECTOR EV", model_years=(2021, 2022))
    official_recall_date = date(2022, 6, 10)
    complaints: list[Complaint] = []

    # Sparse baseline followed by a persistent pre-recall acceleration.
    dates = [
        date(2022, 1, 10),
        date(2022, 2, 22),
        date(2022, 4, 29),
        date(2022, 5, 6),
        date(2022, 5, 13),
        date(2022, 5, 20),
        date(2022, 5, 22),
        date(2022, 5, 27),
        date(2022, 5, 29),
        date(2022, 6, 1),
        date(2022, 6, 3),
        date(2022, 6, 5),
        date(2022, 6, 7),
        date(2022, 6, 8),
    ]
    narratives = [
        "Vehicle suddenly lost motive power while driving and coasted to the shoulder.",
        "The electric vehicle shut off on the highway and would not accelerate.",
        "Propulsion power was lost while the vehicle was in motion.",
        "Vehicle stalled at speed after a high voltage system warning.",
    ]
    for index, received in enumerate(dates, start=1):
        complaints.append(
            Complaint(
                odi_number=f"DEMO{index:04d}",
                vehicle=Vehicle(make=vehicle.make, model=vehicle.model, model_years=(2021 if index % 2 else 2022,)),
                manufacturer="Demo Motors",
                received_date=received,
                incident_date=received - timedelta(days=1),
                components=("POWER TRAIN", "ELECTRICAL SYSTEM"),
                narrative=narratives[index % len(narratives)],
                crash=index == 12,
                injuries=0,
                fire=False,
                deaths=0,
                raw_payload={"synthetic": True},
            )
        )

    # A low-severity unrelated cluster acts as a negative control.
    for index, received in enumerate((date(2022, 4, 5), date(2022, 5, 4), date(2022, 6, 2)), start=100):
        complaints.append(
            Complaint(
                odi_number=f"DEMO{index:04d}",
                vehicle=Vehicle(make=vehicle.make, model=vehicle.model, model_years=(2022,)),
                manufacturer="Demo Motors",
                received_date=received,
                components=("EQUIPMENT",),
                narrative="The infotainment screen rebooted and audio was temporarily unavailable.",
                raw_payload={"synthetic": True},
            )
        )

    target_recall = Recall(
        campaign_number="22V999000",
        vehicle=vehicle,
        manufacturer="Demo Motors",
        report_received_date=official_recall_date,
        component="ELECTRICAL SYSTEM: PROPULSION: HIGH VOLTAGE",
        summary="A high-voltage contactor may overheat and open, resulting in a loss of motive power while driving.",
        consequence="A loss of motive power can increase the risk of a crash.",
        remedy="Dealers will replace affected high-voltage components.",
        raw_payload={"synthetic": True},
    )
    return vehicle, complaints, [target_recall], target_recall, official_recall_date
