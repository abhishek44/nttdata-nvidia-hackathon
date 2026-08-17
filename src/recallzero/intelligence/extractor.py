from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any, Protocol

from pydantic import ValidationError

from recallzero.intelligence.nim_client import NIMClient, NIMError
from recallzero.models import Complaint, ExtractionMethod, FailureSignature

logger = logging.getLogger(__name__)


class FailureExtractor(Protocol):
    async def extract(self, complaint: Complaint) -> FailureSignature: ...


SYSTEM_ALIASES: dict[str, tuple[str, ...]] = {
    "SERVICE BRAKES": ("brake", "braking", "pedal", "abs"),
    "STEERING": ("steer", "steering", "eps", "power steering"),
    "POWER TRAIN": (
        "power train",
        "powertrain",
        "transmission",
        "propulsion",
        "motive power",
        "stall",
        "shut off",
        "lost power",
        "loss of power",
        "would not accelerate",
    ),
    "ENGINE": ("engine", "motor", "stall", "shut off", "loss of power"),
    "ELECTRICAL SYSTEM": ("electrical", "battery", "voltage", "charging", "display", "software"),
    "AIR BAGS": ("airbag", "air bag", "srs"),
    "FUEL/PROPULSION SYSTEM": ("fuel", "gasoline", "propulsion", "accelerat"),
    "VISIBILITY": ("windshield", "sunroof", "glass", "visibility", "camera"),
    "SUSPENSION": ("suspension", "strut", "control arm"),
    "SEAT BELTS": ("seat belt", "seatbelt", "restraint"),
}


class HeuristicFailureExtractor:
    def _system(self, complaint: Complaint, text: str) -> str:
        if complaint.components:
            # A complaint can carry multiple NHTSA components. Prefer the component
            # whose vocabulary is best supported by the narrative instead of relying
            # on alphabetical normalization order.
            ranked: list[tuple[int, int, str]] = []
            for index, component in enumerate(complaint.components):
                component_lower = component.lower()
                keywords: set[str] = set()
                for known_system, aliases in SYSTEM_ALIASES.items():
                    if known_system in component or component in known_system:
                        keywords.update(aliases)
                if not keywords:
                    keywords.update(token for token in re.findall(r"[a-z0-9]+", component_lower) if len(token) > 3)
                score = sum(text.count(keyword) for keyword in keywords)
                ranked.append((score, -index, component))
            return max(ranked)[2]

        scores = Counter()
        for system, keywords in SYSTEM_ALIASES.items():
            scores[system] = sum(text.count(keyword) for keyword in keywords)
        best, score = scores.most_common(1)[0] if scores else ("UNKNOWN", 0)
        return best if score else "UNKNOWN"

    def _failure_mode(self, text: str, system: str) -> tuple[str, str | None]:
        rules: list[tuple[tuple[str, ...], str, str | None]] = [
            (("lost braking", "brakes failed", "brake failure", "pedal went soft", "hard brake pedal", "stopping distance"), "LOSS OF BRAKING EFFECTIVENESS", "Reduced or unavailable braking"),
            (("unexpected braking", "braked on its own", "phantom braking", "automatic emergency braking"), "UNINTENDED BRAKING", "Vehicle brakes without driver request"),
            (("steering locked", "wheel locked", "unable to steer"), "STEERING LOCK", "Steering input unavailable"),
            (("power steering stopped", "steering assist", "steering became hard", "eps warning"), "LOSS OF STEERING ASSIST", "Increased steering effort"),
            (("lost power", "loss of power", "lost propulsion", "loss of propulsion", "lost motive power", "loss of motive power", "propulsion power", "shut off", "stalled", "stalling"), "LOSS OF MOTIVE POWER", "Vehicle unable to maintain propulsion"),
            (("fire", "smoke", "burning", "overheat", "thermal"), "THERMAL EVENT", "Fire, smoke, burning odor, or overheating"),
            (("airbag did not deploy", "air bag did not deploy", "non-deployment", "failed to deploy"), "AIRBAG NON-DEPLOYMENT", "Airbag did not deploy during reported crash"),
            (("airbag warning", "air bag warning", "srs light"), "AIRBAG WARNING", "Airbag system warning indicated"),
            (("unintended acceleration", "accelerated on its own", "sudden acceleration"), "UNINTENDED ACCELERATION", "Unexpected vehicle acceleration"),
            (("screen went blank", "display shut off", "instrument cluster blank"), "DISPLAY LOSS", "Safety information display unavailable"),
            (("charging failed", "will not charge", "unable to charge"), "CHARGING FAILURE", "Vehicle cannot charge"),
            (("warning light", "warning came on", "fault message"), "WARNING OR FAULT INDICATION", "Vehicle indicated a system fault"),
        ]
        for keywords, mode, symptom in rules:
            if any(keyword in text for keyword in keywords):
                return mode, symptom
        if system != "UNKNOWN":
            return f"{system} MALFUNCTION", None
        return "UNSPECIFIED VEHICLE MALFUNCTION", None

    def _operating_state(self, text: str) -> str | None:
        if any(term in text for term in ("while driving", "highway", "mph", "in motion", "traveling")):
            return "VEHICLE IN MOTION"
        if any(term in text for term in ("parked", "while parked")):
            return "PARKED"
        if any(term in text for term in ("starting", "start the vehicle", "would not start")):
            return "STARTUP"
        if "charging" in text or "charge" in text:
            return "CHARGING"
        return None

    def _indicators(self, complaint: Complaint, text: str, failure_mode: str, operating_state: str | None) -> tuple[str, ...]:
        indicators: set[str] = set()
        if complaint.crash:
            indicators.add("crash_reported")
        if complaint.fire or any(term in text for term in ("fire", "flames", "smoke", "burning")):
            indicators.add("fire_or_thermal_event")
        if complaint.injuries:
            indicators.add("injury_reported")
        if complaint.deaths:
            indicators.add("fatality_reported")
        mapping = {
            "LOSS OF BRAKING EFFECTIVENESS": "loss_of_braking",
            "LOSS OF STEERING ASSIST": "loss_of_steering",
            "STEERING LOCK": "loss_of_steering",
            "LOSS OF MOTIVE POWER": "loss_of_motive_power",
            "AIRBAG NON-DEPLOYMENT": "restraint_failure",
            "UNINTENDED ACCELERATION": "unintended_acceleration",
            "UNINTENDED BRAKING": "unintended_braking",
        }
        if failure_mode in mapping:
            indicators.add(mapping[failure_mode])
        if operating_state == "VEHICLE IN MOTION":
            indicators.add("vehicle_in_motion")
        if any(term in text for term in ("loss of control", "could not control", "unable to control")):
            indicators.add("loss_of_control")
        return tuple(sorted(indicators))

    async def extract(self, complaint: Complaint) -> FailureSignature:
        text = " ".join(complaint.narrative.lower().split())
        system = self._system(complaint, text)
        failure_mode, symptom = self._failure_mode(text, system)
        operating_state = self._operating_state(text)
        indicators = self._indicators(complaint, text, failure_mode, operating_state)
        consequence = symptom
        confidence = 0.72 if failure_mode != "UNSPECIFIED VEHICLE MALFUNCTION" else 0.4
        return FailureSignature(
            complaint_id=complaint.odi_number,
            system=system,
            subsystem=None,
            failure_mode=failure_mode,
            symptom=symptom,
            operating_state=operating_state,
            consequence=consequence,
            severity_indicators=indicators,
            confidence=confidence,
            extraction_method=ExtractionMethod.HEURISTIC,
        )


EXTRACTION_SYSTEM_PROMPT = """You normalize vehicle safety complaint language into a structured failure signature.
Use only information present in the complaint. Do not infer a confirmed defect, root cause, recall status, trend,
causation, complaint count, or risk score. Return exactly one JSON object and no markdown.

Required JSON keys:
- system: broad vehicle system in concise uppercase terminology
- subsystem: narrower subsystem or null
- failure_mode: concise normalized failure mode
- symptom: observed symptom or null
- operating_state: condition such as VEHICLE IN MOTION, PARKED, STARTUP, CHARGING, or null
- consequence: observed or directly stated consequence, or null
- severity_indicators: array containing only supported labels from:
  crash_reported, fire_or_thermal_event, injury_reported, fatality_reported, loss_of_braking,
  loss_of_steering, loss_of_motive_power, loss_of_control, restraint_failure,
  unintended_acceleration, unintended_braking, vehicle_in_motion
- confidence: number between 0 and 1 reflecting confidence in normalization
"""


class NIMFailureExtractor:
    def __init__(self, client: NIMClient, model_name: str):
        self.client = client
        self.model_name = model_name

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start < 0 or end <= start:
                raise
            value = json.loads(cleaned[start : end + 1])
        if not isinstance(value, dict):
            raise ValueError("Extraction response must be a JSON object")
        return value

    async def extract(self, complaint: Complaint) -> FailureSignature:
        incident_context = {
            "odi_number": complaint.odi_number,
            "components_reported_by_nhtsa": list(complaint.components),
            "crash_reported": complaint.crash,
            "fire_reported": complaint.fire,
            "injuries_reported": complaint.injuries,
            "deaths_reported": complaint.deaths,
            "narrative": complaint.narrative,
        }
        content = await self.client.chat_completion(
            model=self.model_name,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(incident_context, ensure_ascii=False)},
            ],
            temperature=0.0,
            max_tokens=650,
        )
        parsed = self._extract_json(content)
        parsed.update(
            {
                "complaint_id": complaint.odi_number,
                "extraction_method": ExtractionMethod.NIM,
                "model_name": self.model_name,
            }
        )
        return FailureSignature.model_validate(parsed)


class HybridFailureExtractor:
    def __init__(
        self,
        *,
        heuristic: HeuristicFailureExtractor,
        nim: NIMFailureExtractor | None,
        concurrency: int = 4,
        fallback_on_error: bool = True,
    ):
        self.heuristic = heuristic
        self.nim = nim
        self.concurrency = concurrency
        self.fallback_on_error = fallback_on_error

    async def extract(self, complaint: Complaint) -> FailureSignature:
        if self.nim is None:
            return await self.heuristic.extract(complaint)
        try:
            return await self.nim.extract(complaint)
        except (NIMError, ValidationError, ValueError, json.JSONDecodeError) as exc:
            if not self.fallback_on_error:
                raise
            logger.warning("NIM extraction failed for ODI %s; using heuristic fallback: %s", complaint.odi_number, exc)
            return await self.heuristic.extract(complaint)

    async def extract_many(
        self,
        complaints: Sequence[Complaint],
        cached: dict[str, FailureSignature] | None = None,
    ) -> list[FailureSignature]:
        cached = cached or {}
        semaphore = asyncio.Semaphore(self.concurrency)

        def _cache_is_usable(signature: FailureSignature) -> bool:
            if self.nim is None:
                return True
            return (
                signature.extraction_method == ExtractionMethod.NIM
                and signature.model_name == self.nim.model_name
            )

        async def _one(complaint: Complaint) -> FailureSignature:
            cached_signature = cached.get(complaint.odi_number)
            if cached_signature is not None and _cache_is_usable(cached_signature):
                return cached_signature
            async with semaphore:
                return await self.extract(complaint)

        return list(await asyncio.gather(*(_one(complaint) for complaint in complaints)))


def extraction_method_counts(signatures: Iterable[FailureSignature]) -> dict[str, int]:
    counts = Counter(signature.extraction_method.value for signature in signatures)
    return dict(sorted(counts.items()))
