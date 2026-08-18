from __future__ import annotations

import re

from recallzero.clients.nvidia_nim import NvidiaNIMClient
from recallzero.models import Complaint, FailureSignature

SYSTEM_PROMPT = """You are an automotive safety complaint normalization engine.
Return JSON only. Do not decide whether a defect exists. Extract only facts supported by the complaint.
Schema:
{
  "system": "UPPERCASE system",
  "subsystem": "UPPERCASE subsystem or null",
  "failure_mode": "UPPERCASE normalized failure mode",
  "operating_state": "VEHICLE_MOVING|STATIONARY|STARTUP|UNKNOWN",
  "consequence": "brief normalized safety consequence or null",
  "symptoms": ["normalized symptom"],
  "severity_indicators": ["CRASH|FIRE|INJURY|LOSS_OF_MOTIVE_POWER|LOSS_OF_STEERING|LOSS_OF_BRAKING|OTHER"],
  "confidence": 0.0
}
"""


class SignatureExtractor:
    def __init__(self, nim: NvidiaNIMClient | None = None):
        self.nim = nim or NvidiaNIMClient()

    def extract(self, complaint: Complaint) -> FailureSignature:
        if self.nim.llm_enabled:
            prompt = (
                f"NHTSA components: {', '.join(complaint.components)}\n"
                f"Crash: {complaint.crash}; Fire: {complaint.fire}; Injuries: {complaint.injuries}; Deaths: {complaint.deaths}\n"
                f"Complaint:\n{complaint.summary}"
            )
            payload = self.nim.chat_json(SYSTEM_PROMPT, prompt)
            return FailureSignature.model_validate(payload)
        return self._heuristic_extract(complaint)

    @staticmethod
    def _heuristic_extract(complaint: Complaint) -> FailureSignature:
        text = complaint.summary.lower()
        component = (complaint.components[0] if complaint.components else "UNKNOWN").upper()
        failure = "OTHER_FAILURE"
        subsystem = None
        consequence = None
        indicators: list[str] = []

        rules = [
            (r"loss of (motive )?power|lost power|shut down|shutdown|stalled|stop safely now", "LOSS_OF_MOTIVE_POWER", "LOSS OF MOTIVE POWER"),
            (r"power steering|steering.*hard|difficult to (turn|steer)|loss of.*steer", "LOSS_OF_STEERING", "REDUCED STEERING CONTROL"),
            (r"brake.*(fail|loss)|loss of.*brak|hard brake pedal", "LOSS_OF_BRAKING", "REDUCED BRAKING"),
            (r"fire|smoke|burn", "THERMAL_EVENT", "FIRE RISK"),
        ]
        for pattern, mode, cons in rules:
            if re.search(pattern, text):
                failure = mode
                consequence = cons
                if mode.startswith("LOSS_OF_"):
                    indicators.append(mode)
                break

        if "battery" in text or "high voltage" in text:
            subsystem = "HIGH_VOLTAGE_BATTERY"
            if component == "UNKNOWN":
                component = "ELECTRICAL SYSTEM"
        elif "power steering" in text or "eps" in text:
            subsystem = "ELECTRIC_POWER_STEERING"
            component = "STEERING"

        if complaint.crash:
            indicators.append("CRASH")
        if complaint.fire:
            indicators.append("FIRE")
        if complaint.injuries:
            indicators.append("INJURY")

        moving = bool(re.search(r"while driving|highway|mph|in motion|freeway", text))
        return FailureSignature(
            system=component,
            subsystem=subsystem,
            failure_mode=failure,
            operating_state="VEHICLE_MOVING" if moving else "UNKNOWN",
            consequence=consequence,
            symptoms=[],
            severity_indicators=sorted(set(indicators)),
            confidence=0.55,
        )
