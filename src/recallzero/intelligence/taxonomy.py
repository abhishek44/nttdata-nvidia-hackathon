from __future__ import annotations

import re
from collections.abc import Iterable

from recallzero.models import Complaint, FailureSignature, Recall


# Controlled, generic failure families. These are intentionally consequence/failure
# oriented rather than campaign-specific so the same taxonomy can be reused across
# makes, models, and historical recalls.
DEFECT_FAMILY_LABELS: dict[str, str] = {
    "LOSS_OF_MOTIVE_POWER": "Loss of motive power / vehicle shutdown",
    "HIGH_VOLTAGE_POWER_DISTRIBUTION": "High-voltage power distribution / contactor failure",
    "NO_START_OR_NO_DRIVE": "No-start / unable to drive or shift",
    "CHARGING_FAILURE": "Charging system failure",
    "UNINTENDED_ACCELERATION": "Unintended acceleration",
    "UNINTENDED_BRAKING": "Unintended braking",
    "LOSS_OF_BRAKING": "Loss or degradation of braking",
    "PARKING_BRAKE_FAILURE": "Parking brake failure",
    "BRAKE_WEAR_OR_VIBRATION": "Brake wear / rotor vibration",
    "LOSS_OF_STEERING": "Loss or degradation of steering",
    "ADAS_CAMERA_UNAVAILABLE": "ADAS camera / sensor unavailable",
    "ADAS_FALSE_INTERVENTION": "ADAS false intervention / unexpected deactivation",
    "RESTRAINT_FAILURE": "Restraint / airbag / seat-belt failure",
    "GLASS_OR_ADHESION": "Glass / windshield / roof adhesion failure",
    "STRUCTURE_CLOSURE_FAILURE": "Door / liftgate / closure failure",
    "DISPLAY_OR_UI_FAILURE": "Display / instrument / user-interface failure",
    "ACCESS_OR_KEY_FAILURE": "Key / access / immobilizer failure",
    "RECALL_SERVICE_ISSUE": "Recall remedy / service availability issue",
    "THERMAL_EVENT": "Fire / smoke / overheating / thermal event",
    "VEHICLE_ROLLAWAY": "Unexpected vehicle rollaway",
    "GENERAL_ELECTRICAL_FAILURE": "General electrical failure",
    "GENERAL_POWERTRAIN_FAILURE": "General powertrain failure",
    "GENERAL_SAFETY_SYSTEM_FAILURE": "General safety-system failure",
    "OTHER": "Other / uncategorized failure",
}


def _norm(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _contains_any(text: str, phrases: Iterable[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _signature_text(signature: FailureSignature) -> str:
    return _norm(
        " ".join(
            value
            for value in (
                signature.system,
                signature.subsystem,
                signature.failure_mode,
                signature.symptom,
                signature.operating_state,
                signature.consequence,
            )
            if value
        )
    )


def derive_defect_family(complaint: Complaint, signature: FailureSignature) -> str:
    """Map a complaint/signature pair to a stable, campaign-agnostic defect family.

    The raw NIM failure mode is retained for evidence. This controlled family is used
    only for grouping, diagnostics, and structured recall matching. Rules deliberately
    use generic automotive language and do not contain make/model/campaign identifiers.
    """

    narrative = _norm(complaint.narrative)
    sig = _signature_text(signature)
    text = f"{sig} {narrative}"

    # Service/remedy complaints should not be mixed with the underlying defect itself.
    if _contains_any(
        text,
        (
            "recall repair delay",
            "recall service delay",
            "recall service not performed",
            "recall not completed",
            "recall refusal",
            "recall part unavailability",
            "parts not available for recall",
            "dealer refuses recall",
            "dealer unable to perform recall",
        ),
    ):
        return "RECALL_SERVICE_ISSUE"

    if _contains_any(text, ("fire", "flames", "smoke", "burning", "overheat", "overheating", "thermal event")):
        return "THERMAL_EVENT"

    if _contains_any(
        text,
        (
            "high voltage battery junction box",
            "high voltage junction box",
            "hv battery junction box",
            "hvbjb",
            "hvjbc",
            "battery junction box",
            "contactor circuit",
            "battery contactor",
            "main contactor",
            "becm failure",
            "battery energy control module",
        ),
    ):
        return "HIGH_VOLTAGE_POWER_DISTRIBUTION"

    if _contains_any(
        text,
        (
            "unintended acceleration",
            "unexpected acceleration",
            "accelerated on its own",
            "accelerated independently",
            "independently accelerated",
            "suddenly accelerated",
            "vehicle accelerated forward",
            "vehicle accelerated backwards",
        ),
    ):
        return "UNINTENDED_ACCELERATION"

    if _contains_any(
        text,
        (
            "unintended braking",
            "unexpected braking",
            "braked on its own",
            "brakes applied on their own",
            "independently brake",
            "independently braked",
            "phantom braking",
            "unexpected rapid deceleration",
        ),
    ):
        return "UNINTENDED_BRAKING"

    if _contains_any(
        text,
        (
            "loss of braking",
            "lost all regenerative braking",
            "brakes failed",
            "brake failure",
            "power brakes failed",
            "failed to stop",
            "did not slow down",
            "no brakes",
            "brake pedal softness",
            "soft brake pedal",
        ),
    ):
        return "LOSS_OF_BRAKING"

    if _contains_any(
        text,
        (
            "parking brake fault",
            "parking brake failure",
            "parking brake stuck",
            "electronic parking brake",
            "stuck disc brake",
        ),
    ):
        return "PARKING_BRAKE_FAILURE"

    if _contains_any(
        text,
        (
            "rotor warping",
            "warped rotors",
            "brake shudder",
            "brake vibration",
            "vibration when braking",
            "brake pad wear",
            "metal on metal",
        ),
    ):
        return "BRAKE_WEAR_OR_VIBRATION"

    if _contains_any(
        text,
        (
            "loss of steering",
            "power steering failed",
            "no power steering",
            "unable to steer",
            "steering locked",
            "steering lock",
            "steering assist failure",
        ),
    ):
        return "LOSS_OF_STEERING"

    if _contains_any(
        text,
        (
            "loss of motive power",
            "lost motive power",
            "lost propulsion",
            "loss of propulsion",
            "lost all power while driving",
            "vehicle shutdown",
            "vehicle shut down",
            "car turned itself off",
            "throttle went dead",
            "no acceleration",
            "unexpected vehicle stall",
            "vehicle stalled",
            "car went dead",
        ),
    ):
        return "LOSS_OF_MOTIVE_POWER"

    if _contains_any(
        text,
        (
            "failure to start",
            "failure_to_start",
            "would not start",
            "will not start",
            "car did not start",
            "car wouldn t start",
            "vehicle would not start",
            "vehicle can t start",
            "would not move",
            "will not move",
            "could not move vehicle",
            "cannot be shifted",
            "could not shift",
            "would not shift",
            "will not exit park",
            "would not exit park",
            "could not get out of park",
            "could not be shifted out of park",
            "gear selection failure",
        ),
    ):
        return "NO_START_OR_NO_DRIVE"

    if _contains_any(text, ("charging failure", "unable to charge", "will not charge", "charge state error", "charging fault")):
        return "CHARGING_FAILURE"

    if _contains_any(
        text,
        (
            "front camera fault",
            "camera fault",
            "camera failure",
            "camera malfunction",
            "pre collision assist not available",
            "pre collision assist unavailable",
            "sensor unavailable",
            "camera unavailable",
            "camera system offline",
        ),
    ):
        return "ADAS_CAMERA_UNAVAILABLE"

    if _contains_any(
        text,
        (
            "adas",
            "bluecruise",
            "adaptive cruise",
            "lane keeping",
            "lane departure",
            "pre collision assist",
            "collision avoidance",
        ),
    ) and _contains_any(
        text,
        (
            "unexpected deactivation",
            "random deactivation",
            "system deactivation",
            "false intervention",
            "unexpected intervention",
            "system unavailable",
            "system inoperative",
            "disengagement",
        ),
    ):
        return "ADAS_FALSE_INTERVENTION"

    if _contains_any(text, ("airbag", "air bag", "seat belt", "seatbelt", "retractor", "restraint")) and _contains_any(
        text, ("failure", "failed", "not deploy", "non deployment", "torn", "ripped", "malfunction")
    ):
        return "RESTRAINT_FAILURE"

    if _contains_any(text, ("windshield", "panoramic roof", "roof glass", "glass")) and _contains_any(
        text, ("adhesion", "detach", "detached", "fracture", "crack", "shatter", "reseal", "urethane")
    ):
        return "GLASS_OR_ADHESION"

    if _contains_any(text, ("door", "tailgate", "liftgate", "hatch")) and _contains_any(
        text, ("open on its own", "opened on its own", "spontaneous opening", "door opening", "door fault", "hinge", "weld")
    ):
        return "STRUCTURE_CLOSURE_FAILURE"

    if _contains_any(text, ("rollaway", "vehicle roll", "unexpected vehicle roll", "rolled down incline")):
        return "VEHICLE_ROLLAWAY"

    if _contains_any(text, ("phone as a key", "paak", "key failure", "key fob", "immobilizer", "phone recognition")):
        return "ACCESS_OR_KEY_FAILURE"

    if _contains_any(text, ("display blackout", "screen went blank", "instrument cluster", "display malfunction", "cluster goes black")):
        return "DISPLAY_OR_UI_FAILURE"

    system_text = _norm(signature.system)
    components = " ".join(_norm(item) for item in complaint.components)
    if "electrical" in system_text or "electrical" in components or "battery" in text:
        return "GENERAL_ELECTRICAL_FAILURE"
    if _contains_any(system_text, ("power train", "powertrain", "propulsion", "transmission")):
        return "GENERAL_POWERTRAIN_FAILURE"
    if _contains_any(text, ("driver assistance", "adas", "collision avoidance", "lane departure")):
        return "GENERAL_SAFETY_SYSTEM_FAILURE"
    return "OTHER"


def derive_recall_defect_families(recall: Recall) -> set[str]:
    """Derive one or more generic failure families from recall text."""

    text = _norm(
        " ".join(
            value
            for value in (recall.component, recall.summary, recall.consequence, recall.remedy, recall.notes)
            if value
        )
    )
    families: set[str] = set()

    def has(*phrases: str) -> bool:
        return _contains_any(text, phrases)

    if has("contactor", "junction box", "high voltage battery", "high voltage"):
        families.add("HIGH_VOLTAGE_POWER_DISTRIBUTION")
    if has("loss of motive power", "loss of propulsion", "stall", "vehicle may lose power", "power loss"):
        families.add("LOSS_OF_MOTIVE_POWER")
    if has("will not start", "no start", "unable to move", "cannot shift"):
        families.add("NO_START_OR_NO_DRIVE")
    if has("unintended acceleration", "unexpected acceleration", "accelerate unexpectedly"):
        families.add("UNINTENDED_ACCELERATION")
    if has("unintended braking", "unexpected braking", "brake without warning"):
        families.add("UNINTENDED_BRAKING")
    if has("brake", "braking") and has("loss", "failure", "reduced", "degrad"):
        families.add("LOSS_OF_BRAKING")
    if has("steering") and has("loss", "failure", "assist"):
        families.add("LOSS_OF_STEERING")
    if has("camera", "pre collision", "collision avoidance", "lane keep", "sensor"):
        families.add("ADAS_CAMERA_UNAVAILABLE")
    if has("windshield", "roof glass", "glass") and has("adhesion", "detach", "separate", "urethane"):
        families.add("GLASS_OR_ADHESION")
    if has("air bag", "airbag", "seat belt", "restraint"):
        families.add("RESTRAINT_FAILURE")
    if has("fire", "thermal", "overheat", "smoke"):
        families.add("THERMAL_EVENT")
    if has("charging", "charger") and has("fail", "unable", "inoperative"):
        families.add("CHARGING_FAILURE")
    return families


def families_related(cluster_family: str, recall_families: set[str]) -> float:
    if not cluster_family or cluster_family == "OTHER" or not recall_families:
        return 0.0
    if cluster_family in recall_families:
        return 1.0
    related_pairs = {
        frozenset(("HIGH_VOLTAGE_POWER_DISTRIBUTION", "LOSS_OF_MOTIVE_POWER")),
        frozenset(("HIGH_VOLTAGE_POWER_DISTRIBUTION", "NO_START_OR_NO_DRIVE")),
        frozenset(("LOSS_OF_MOTIVE_POWER", "NO_START_OR_NO_DRIVE")),
        frozenset(("ADAS_CAMERA_UNAVAILABLE", "ADAS_FALSE_INTERVENTION")),
        frozenset(("LOSS_OF_BRAKING", "PARKING_BRAKE_FAILURE")),
    }
    return 0.82 if any(frozenset((cluster_family, item)) in related_pairs for item in recall_families) else 0.0
