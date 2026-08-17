from __future__ import annotations

import re
from collections.abc import Iterable

from recallzero.models import Complaint, FailureSignature, Recall


# 0.3.2 separates root/mechanism language from driver-visible consequence language.
# This prevents one underlying mechanism from being fragmented only because it can
# present as shutdown, no-start, reduced propulsion, or another consequence.
FAILURE_MECHANISM_LABELS: dict[str, str] = {
    "HIGH_VOLTAGE_POWER_DISTRIBUTION": "High-voltage power distribution / contactor",
    "LOW_VOLTAGE_ELECTRICAL": "Low-voltage electrical supply",
    "CHARGING_SYSTEM": "Charging system",
    "PROPULSION_CONTROL": "Propulsion / powertrain control",
    "BRAKE_SYSTEM": "Brake system",
    "STEERING_SYSTEM": "Steering system",
    "ADAS_SENSING": "ADAS sensing / perception",
    "RESTRAINT_SYSTEM": "Restraint system",
    "GLASS_ADHESION": "Glass / roof / windshield adhesion",
    "STRUCTURE_CLOSURE": "Door / liftgate / closure",
    "ACCESS_CONTROL": "Key / access / immobilizer",
    "DISPLAY_CONTROL": "Display / instrument / user interface",
    "THERMAL_SYSTEM": "Thermal / overheating",
    "RECALL_SERVICE": "Recall remedy / service availability",
    "GENERAL_ELECTRICAL": "General electrical",
    "GENERAL_POWERTRAIN": "General powertrain",
    "GENERAL_SAFETY": "General safety system",
    "OTHER": "Other / unknown mechanism",
}

CONSEQUENCE_FAMILY_LABELS: dict[str, str] = {
    "LOSS_OF_MOTIVE_POWER": "Loss of motive power / vehicle shutdown",
    "NO_START_OR_NO_DRIVE": "No-start / unable to drive or shift",
    "REDUCED_PROPULSION": "Reduced propulsion / limp mode",
    "CHARGING_FAILURE": "Unable to charge / charging interruption",
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

# Kept for API/backward compatibility with 0.3.1 JSON and tests.
DEFECT_FAMILY_LABELS: dict[str, str] = {
    **CONSEQUENCE_FAMILY_LABELS,
    "HIGH_VOLTAGE_POWER_DISTRIBUTION": FAILURE_MECHANISM_LABELS["HIGH_VOLTAGE_POWER_DISTRIBUTION"],
}

META_ELIGIBLE_MECHANISMS = {
    # Mechanisms with sufficiently specific root-cause language to support
    # cross-component aggregation. Consequence-oriented brake/steering/restraint
    # categories stay as child signals until a stronger root mechanism is explicit.
    "HIGH_VOLTAGE_POWER_DISTRIBUTION",
    "LOW_VOLTAGE_ELECTRICAL",
    "CHARGING_SYSTEM",
    "PROPULSION_CONTROL",
    "ADAS_SENSING",
    "GLASS_ADHESION",
    "STRUCTURE_CLOSURE",
    "ACCESS_CONTROL",
    "DISPLAY_CONTROL",
    "THERMAL_SYSTEM",
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


def _combined_text(complaint: Complaint, signature: FailureSignature) -> str:
    return f"{_signature_text(signature)} {_norm(complaint.narrative)}"


def _derive_failure_mechanism_raw(complaint: Complaint, signature: FailureSignature) -> str:
    text = _combined_text(complaint, signature)
    system_text = _norm(signature.system)
    components = " ".join(_norm(item) for item in complaint.components)

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
        return "RECALL_SERVICE"

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
            "bussed electrical center",
            "becm failure",
            "battery energy control module",
        ),
    ):
        return "HIGH_VOLTAGE_POWER_DISTRIBUTION"

    if _contains_any(text, ("12v battery", "12 volt battery", "low voltage battery", "12v system")):
        return "LOW_VOLTAGE_ELECTRICAL"

    if _contains_any(text, ("charging port", "charger", "charging module", "charge state", "dc fast charge", "dcfc")):
        return "CHARGING_SYSTEM"

    if _contains_any(text, ("fire", "flames", "smoke", "burning", "overheat", "overheating", "thermal event", "melted")):
        return "THERMAL_SYSTEM"

    if _contains_any(text, ("phone as a key", "paak", "key fob", "immobilizer", "phone recognition")):
        return "ACCESS_CONTROL"

    if _contains_any(text, ("display blackout", "screen went blank", "screen black", "display malfunction", "instrument cluster failure", "instrument cluster blackout", "display panel failure")):
        return "DISPLAY_CONTROL"

    if _contains_any(text, ("windshield", "panoramic roof", "roof glass", "glass")) and _contains_any(text, ("adhesion", "urethane", "detach", "separate", "bond")):
        return "GLASS_ADHESION"

    if _contains_any(text, ("door", "tailgate", "liftgate", "hatch")) and _contains_any(text, ("latch failure", "door fault", "opened on its own", "open on its own", "spontaneous opening", "hinge failure", "weld failure")):
        return "STRUCTURE_CLOSURE"

    if _contains_any(text, ("airbag", "air bag", "seat belt", "seatbelt", "retractor", "restraint")) and _contains_any(text, ("failed", "failure", "not deploy", "retractor", "torn", "malfunction")):
        return "RESTRAINT_SYSTEM"

    if _contains_any(text, ("camera fault", "camera failure", "camera unavailable", "sensor fault", "sensor failure", "sensor unavailable", "bluecruise unavailable", "pre collision assist unavailable")):
        return "ADAS_SENSING"

    if _contains_any(text, ("brake failure", "brakes failed", "loss of braking", "regenerative braking", "parking brake", "brake pad", "brake rotor", "abs failure")):
        return "BRAKE_SYSTEM"

    if _contains_any(text, ("power steering", "steering assist", "steering locked", "steering lock", "loss of steering", "steering failure")):
        return "STEERING_SYSTEM"

    if _contains_any(text, ("powertrain control module", "propulsion control module", "electric drive unit", "drive unit failure", "transmission failure", "motor failure")):
        return "PROPULSION_CONTROL"

    if "electrical" in system_text or "electrical" in components or "battery" in text:
        return "GENERAL_ELECTRICAL"
    if _contains_any(system_text, ("power train", "powertrain", "propulsion", "transmission")):
        return "GENERAL_POWERTRAIN"
    if _contains_any(text, ("driver assistance", "adas", "collision avoidance", "lane departure")):
        return "GENERAL_SAFETY"
    return "OTHER"


def derive_consequence_family(complaint: Complaint, signature: FailureSignature) -> str:
    text = _combined_text(complaint, signature)
    system_text = _norm(signature.system)
    components = " ".join(_norm(item) for item in complaint.components)

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
    if _contains_any(text, ("fire", "flames", "smoke", "burning", "overheat", "overheating", "thermal event", "melted")):
        return "THERMAL_EVENT"
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
    if _contains_any(text, ("parking brake fault", "parking brake failure", "parking brake stuck", "stuck disc brake")):
        return "PARKING_BRAKE_FAILURE"
    if _contains_any(text, ("rotor warping", "warped rotors", "brake shudder", "brake vibration", "brake pad wear", "metal on metal")):
        return "BRAKE_WEAR_OR_VIBRATION"
    if _contains_any(text, ("loss of steering", "power steering failed", "no power steering", "unable to steer", "steering locked", "steering lock")):
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
            "unexpected vehicle stall",
            "vehicle stalled",
            "car went dead",
            "lost all power",
        ),
    ):
        return "LOSS_OF_MOTIVE_POWER"
    if _contains_any(text, ("reduced power", "reduced propulsion", "limp mode", "limited acceleration", "power reduction")):
        return "REDUCED_PROPULSION"
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
            "will not go into drive",
            "would not go into drive",
            "unable to drive",
        ),
    ):
        return "NO_START_OR_NO_DRIVE"
    if _contains_any(text, ("charging failure", "unable to charge", "will not charge", "charge state error", "charging fault")):
        return "CHARGING_FAILURE"
    if _contains_any(text, ("front camera fault", "camera fault", "camera failure", "camera malfunction", "pre collision assist not available", "sensor unavailable", "camera unavailable")):
        return "ADAS_CAMERA_UNAVAILABLE"
    if _contains_any(text, ("adas", "bluecruise", "adaptive cruise", "lane keeping", "lane departure", "pre collision assist", "collision avoidance")) and _contains_any(
        text,
        ("unexpected deactivation", "random deactivation", "system deactivation", "false intervention", "unexpected intervention", "system unavailable", "system inoperative", "disengagement"),
    ):
        return "ADAS_FALSE_INTERVENTION"
    if _contains_any(text, ("airbag", "air bag", "seat belt", "seatbelt", "retractor", "restraint")) and _contains_any(text, ("failure", "failed", "not deploy", "non deployment", "torn", "ripped", "malfunction")):
        return "RESTRAINT_FAILURE"
    if _contains_any(text, ("windshield", "panoramic roof", "roof glass", "glass")) and _contains_any(text, ("adhesion", "detach", "detached", "fracture", "crack", "shatter", "reseal", "urethane")):
        return "GLASS_OR_ADHESION"
    if _contains_any(text, ("door", "tailgate", "liftgate", "hatch")) and _contains_any(text, ("open on its own", "opened on its own", "spontaneous opening", "door opening", "door fault", "hinge", "weld")):
        return "STRUCTURE_CLOSURE_FAILURE"
    if _contains_any(text, ("rollaway", "vehicle roll", "unexpected vehicle roll", "rolled down incline")):
        return "VEHICLE_ROLLAWAY"
    if _contains_any(text, ("phone as a key", "paak", "key failure", "key fob", "immobilizer", "phone recognition")):
        return "ACCESS_OR_KEY_FAILURE"
    if _contains_any(text, ("display blackout", "screen went blank", "instrument cluster", "display malfunction", "cluster goes black")):
        return "DISPLAY_OR_UI_FAILURE"
    if "electrical" in system_text or "electrical" in components or "battery" in text:
        return "GENERAL_ELECTRICAL_FAILURE"
    if _contains_any(system_text, ("power train", "powertrain", "propulsion", "transmission")):
        return "GENERAL_POWERTRAIN_FAILURE"
    if _contains_any(text, ("driver assistance", "adas", "collision avoidance", "lane departure")):
        return "GENERAL_SAFETY_SYSTEM_FAILURE"
    return "OTHER"


# Narrow 0.3.3a consistency guard. These mappings cover mechanisms whose observed
# consequences should stay within a small domain. If a specific mechanism conflicts
# with a clearly classified consequence, downgrade to a broad component-level
# mechanism rather than allowing the contradictory assignment into meta aggregation.
_SPECIFIC_MECHANISM_CONSEQUENCES: dict[str, set[str]] = {
    "BRAKE_SYSTEM": {
        "LOSS_OF_BRAKING",
        "PARKING_BRAKE_FAILURE",
        "BRAKE_WEAR_OR_VIBRATION",
        "UNINTENDED_BRAKING",
    },
    "STEERING_SYSTEM": {"LOSS_OF_STEERING"},
    "RESTRAINT_SYSTEM": {"RESTRAINT_FAILURE"},
    "ADAS_SENSING": {
        "ADAS_CAMERA_UNAVAILABLE",
        "ADAS_FALSE_INTERVENTION",
        "UNINTENDED_BRAKING",
    },
    "GLASS_ADHESION": {"GLASS_OR_ADHESION"},
    "STRUCTURE_CLOSURE": {"STRUCTURE_CLOSURE_FAILURE"},
    "ACCESS_CONTROL": {"ACCESS_OR_KEY_FAILURE"},
    "DISPLAY_CONTROL": {"DISPLAY_OR_UI_FAILURE"},
    "THERMAL_SYSTEM": {"THERMAL_EVENT"},
    "RECALL_SERVICE": {"RECALL_SERVICE_ISSUE"},
}


def _general_mechanism_for_context(complaint: Complaint, signature: FailureSignature) -> str:
    system_text = _norm(signature.system)
    components = " ".join(_norm(item) for item in complaint.components)
    combined = f"{system_text} {components}"
    if "electrical" in combined:
        return "GENERAL_ELECTRICAL"
    if _contains_any(combined, ("power train", "powertrain", "fuel propulsion", "propulsion", "engine", "transmission")):
        return "GENERAL_POWERTRAIN"
    if _contains_any(combined, ("driver assistance", "forward collision", "vehicle speed control", "collision avoidance", "adas")):
        return "GENERAL_SAFETY"
    return "OTHER"


def mechanism_is_consistent(mechanism: str, consequence: str) -> bool:
    """Return whether a specific mechanism is compatible with a classified consequence.

    Broad mechanisms and the multi-presentation EV mechanisms intentionally remain
    unconstrained in this small slice. The guard only catches strong contradictions
    such as BRAKE_SYSTEM + NO_START_OR_NO_DRIVE.
    """

    allowed = _SPECIFIC_MECHANISM_CONSEQUENCES.get(mechanism)
    if allowed is None or consequence == "OTHER":
        return True
    return consequence in allowed


def derive_failure_mechanism(complaint: Complaint, signature: FailureSignature) -> str:
    mechanism = _derive_failure_mechanism_raw(complaint, signature)
    consequence = derive_consequence_family(complaint, signature)
    if mechanism_is_consistent(mechanism, consequence):
        return mechanism
    return _general_mechanism_for_context(complaint, signature)


def derive_defect_family(complaint: Complaint, signature: FailureSignature) -> str:
    """Backward-compatible 0.3.1 family.

    A known mechanism takes precedence when it was historically represented as a
    defect family (notably high-voltage power distribution). Otherwise the driver-
    visible consequence family is returned. New 0.3.2 logic uses both axes directly.
    """

    mechanism = derive_failure_mechanism(complaint, signature)
    consequence = derive_consequence_family(complaint, signature)
    if mechanism == "HIGH_VOLTAGE_POWER_DISTRIBUTION":
        return mechanism
    if consequence != "OTHER":
        return consequence
    legacy = {
        "LOW_VOLTAGE_ELECTRICAL": "GENERAL_ELECTRICAL_FAILURE",
        "GENERAL_ELECTRICAL": "GENERAL_ELECTRICAL_FAILURE",
        "GENERAL_POWERTRAIN": "GENERAL_POWERTRAIN_FAILURE",
        "GENERAL_SAFETY": "GENERAL_SAFETY_SYSTEM_FAILURE",
        "ACCESS_CONTROL": "ACCESS_OR_KEY_FAILURE",
        "DISPLAY_CONTROL": "DISPLAY_OR_UI_FAILURE",
        "GLASS_ADHESION": "GLASS_OR_ADHESION",
        "STRUCTURE_CLOSURE": "STRUCTURE_CLOSURE_FAILURE",
        "RECALL_SERVICE": "RECALL_SERVICE_ISSUE",
        "THERMAL_SYSTEM": "THERMAL_EVENT",
    }
    return legacy.get(mechanism, "OTHER")


def derive_recall_axes(recall: Recall) -> tuple[set[str], set[str]]:
    text = _norm(
        " ".join(
            value
            for value in (recall.component, recall.summary, recall.consequence, recall.remedy, recall.notes)
            if value
        )
    )
    mechanisms: set[str] = set()
    consequences: set[str] = set()

    def has(*phrases: str) -> bool:
        return _contains_any(text, phrases)

    if has("contactor", "junction box", "high voltage battery", "high voltage"):
        mechanisms.add("HIGH_VOLTAGE_POWER_DISTRIBUTION")
    if has("12 volt", "12v", "low voltage battery"):
        mechanisms.add("LOW_VOLTAGE_ELECTRICAL")
    if has("charging", "charger"):
        mechanisms.add("CHARGING_SYSTEM")
    if has("brake", "braking"):
        mechanisms.add("BRAKE_SYSTEM")
    if has("steering"):
        mechanisms.add("STEERING_SYSTEM")
    if has("camera", "sensor", "pre collision", "collision avoidance", "lane keep"):
        mechanisms.add("ADAS_SENSING")
    if has("windshield", "roof glass", "glass"):
        mechanisms.add("GLASS_ADHESION")
    if has("air bag", "airbag", "seat belt", "restraint"):
        mechanisms.add("RESTRAINT_SYSTEM")
    if has("fire", "thermal", "overheat", "smoke", "melt"):
        mechanisms.add("THERMAL_SYSTEM")

    if has("loss of motive power", "loss of propulsion", "stall", "vehicle may lose power", "power loss"):
        consequences.add("LOSS_OF_MOTIVE_POWER")
    if has("reduced power", "reduced propulsion", "limp mode"):
        consequences.add("REDUCED_PROPULSION")
    if has("will not start", "no start", "unable to move", "cannot shift"):
        consequences.add("NO_START_OR_NO_DRIVE")
    if has("unintended acceleration", "unexpected acceleration", "accelerate unexpectedly"):
        consequences.add("UNINTENDED_ACCELERATION")
    if has("unintended braking", "unexpected braking", "brake without warning"):
        consequences.add("UNINTENDED_BRAKING")
    if has("brake", "braking") and has("loss", "failure", "reduced", "degrad"):
        consequences.add("LOSS_OF_BRAKING")
    if has("steering") and has("loss", "failure", "assist"):
        consequences.add("LOSS_OF_STEERING")
    if has("camera", "pre collision", "collision avoidance", "lane keep", "sensor"):
        consequences.add("ADAS_CAMERA_UNAVAILABLE")
    if has("windshield", "roof glass", "glass") and has("adhesion", "detach", "separate", "urethane"):
        consequences.add("GLASS_OR_ADHESION")
    if has("air bag", "airbag", "seat belt", "restraint"):
        consequences.add("RESTRAINT_FAILURE")
    if has("fire", "thermal", "overheat", "smoke"):
        consequences.add("THERMAL_EVENT")
    if has("charging", "charger") and has("fail", "unable", "inoperative"):
        consequences.add("CHARGING_FAILURE")
    return mechanisms, consequences


def derive_recall_defect_families(recall: Recall) -> set[str]:
    mechanisms, consequences = derive_recall_axes(recall)
    return mechanisms | consequences


def _related_score(value: str, candidates: set[str], related_pairs: set[frozenset[str]]) -> float:
    if not value or value == "OTHER" or not candidates:
        return 0.0
    if value in candidates:
        return 1.0
    return 0.82 if any(frozenset((value, item)) in related_pairs for item in candidates) else 0.0


def mechanism_related(mechanism: str, recall_mechanisms: set[str]) -> float:
    related_pairs = {
        frozenset(("HIGH_VOLTAGE_POWER_DISTRIBUTION", "CHARGING_SYSTEM")),
        frozenset(("LOW_VOLTAGE_ELECTRICAL", "GENERAL_ELECTRICAL")),
        frozenset(("PROPULSION_CONTROL", "GENERAL_POWERTRAIN")),
        frozenset(("ADAS_SENSING", "GENERAL_SAFETY")),
    }
    return _related_score(mechanism, recall_mechanisms, related_pairs)


def consequence_related(consequence: str, recall_consequences: set[str]) -> float:
    related_pairs = {
        frozenset(("LOSS_OF_MOTIVE_POWER", "NO_START_OR_NO_DRIVE")),
        frozenset(("LOSS_OF_MOTIVE_POWER", "REDUCED_PROPULSION")),
        frozenset(("NO_START_OR_NO_DRIVE", "REDUCED_PROPULSION")),
        frozenset(("ADAS_CAMERA_UNAVAILABLE", "ADAS_FALSE_INTERVENTION")),
        frozenset(("LOSS_OF_BRAKING", "PARKING_BRAKE_FAILURE")),
    }
    return _related_score(consequence, recall_consequences, related_pairs)


def families_related(cluster_family: str, recall_families: set[str]) -> float:
    # Backward compatibility for external callers.
    if cluster_family in FAILURE_MECHANISM_LABELS:
        return mechanism_related(cluster_family, recall_families)
    return consequence_related(cluster_family, recall_families)
