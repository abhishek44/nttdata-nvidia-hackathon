from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from recallzero.investigation import EngineeringBriefRenderer
from recallzero.models import DefectSignal


class DemoActionError(RuntimeError):
    """Presentation action could not be completed safely."""


@dataclass(frozen=True, slots=True)
class ActionRecommendation:
    code: str
    title: str
    summary: str


def _recommendation(signal: DefectSignal) -> ActionRecommendation:
    if signal.risk.alert:
        return ActionRecommendation(
            code="ENGINEERING_REVIEW",
            title="Escalate for engineering review",
            summary=(
                "Review the supporting ODI records, confirm the failure pattern, and decide whether "
                "additional engineering investigation is warranted. RecallZero ranks evidence; it does not declare a defect."
            ),
        )
    if signal.risk.final_score >= 60:
        return ActionRecommendation(
            code="PRIORITY_MONITOR",
            title="Priority monitoring",
            summary=(
                "Keep the lineage under review and inspect new complaint evidence as it arrives. "
                "The deterministic alert gate has not been met."
            ),
        )
    return ActionRecommendation(
        code="CONTINUE_MONITORING",
        title="Continue monitoring",
        summary=(
            "No escalation is recommended from this snapshot. Preserve the evidence trail and reevaluate if the pattern changes."
        ),
    )


def build_investigation_packet(
    signal_payload: dict[str, Any],
    *,
    source_label: str = "RecallZero demo",
    historical_outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a business-facing packet without changing detector behavior.

    The packet is derived entirely from an already-computed DefectSignal. No LLM is
    called and no score is recalculated.
    """

    try:
        signal = DefectSignal.model_validate(signal_payload)
    except Exception as exc:  # pydantic raises several validation subtypes
        raise DemoActionError(f"Signal payload is not a valid persisted DefectSignal: {exc}") from exc

    recommendation = _recommendation(signal)
    evidence = list(signal.evidence)
    crash_reports = sum(1 for item in evidence if item.crash)
    fire_reports = sum(1 for item in evidence if item.fire)
    injury_reports = sum(1 for item in evidence if (item.injuries or 0) > 0)
    death_reports = sum(1 for item in evidence if (item.deaths or 0) > 0)
    factor_rows = sorted(
        (
            {
                "name": item.name,
                "score": round(float(item.score), 2),
                "weight": round(float(item.weight), 4),
                "contribution": round(float(item.contribution), 2),
                "explanation": item.explanation,
            }
            for item in signal.risk.factors
        ),
        key=lambda row: row["contribution"],
        reverse=True,
    )

    recall_status = (
        {
            "visible_recall_matched": True,
            "campaign_number": signal.recall_match.campaign_number,
            "similarity": round(float(signal.recall_match.score), 4),
            "explanation": signal.recall_match.reason,
        }
        if signal.recall_match.matched
        else {
            "visible_recall_matched": False,
            "campaign_number": None,
            "similarity": round(float(signal.recall_match.score), 4),
            "explanation": signal.recall_match.reason,
        }
    )

    packet: dict[str, Any] = {
        "packet_type": "RecallZero Engineering Investigation",
        "source_label": source_label,
        "detector_values_recomputed": False,
        "ai_generated_recommendation": False,
        "vehicle": signal.vehicle.model_dump(mode="json"),
        "cutoff_date": signal.cutoff_date.isoformat(),
        "signal": {
            "signal_id": signal.signal_id,
            "lineage_id": signal.lineage_id,
            "issue": signal.cluster.label,
            "signal_scope": signal.signal_scope,
            "system": signal.cluster.system,
            "failure_mode": signal.cluster.failure_mode,
            "failure_mechanism": signal.cluster.failure_mechanism,
            "consequence_family": signal.cluster.consequence_family,
        },
        "priority": {
            "level": signal.risk.level.value,
            "risk_score": round(float(signal.risk.final_score), 2),
            "alert": bool(signal.risk.alert),
            "threshold": round(float(signal.risk.threshold), 2),
            "rationale": signal.risk.rationale,
        },
        "evidence": {
            "supporting_complaints": signal.cluster.evidence_count,
            "recent_window_count": signal.trend.recent_count,
            "baseline_window_count": signal.trend.baseline_count,
            "trend_ratio": round(float(signal.trend.trend_ratio), 2),
            "crash_report_count": crash_reports,
            "fire_report_count": fire_reports,
            "injury_report_count": injury_reports,
            "death_report_count": death_reports,
            "odi_numbers": [item.complaint_id for item in evidence],
        },
        "risk_factors": factor_rows,
        "visible_recall_cross_reference": recall_status,
        "recommended_action": asdict(recommendation),
        "guardrails": [
            "This packet ranks a complaint pattern for investigation; it does not declare that a safety defect exists.",
            "Associated crash, fire, injury, or death flags do not establish causation.",
            "Risk values are copied from the persisted deterministic detector signal and are not recalculated here.",
        ],
    }
    if historical_outcome:
        packet["historical_outcome"] = historical_outcome

    packet["markdown"] = render_investigation_markdown(signal, packet)
    return packet


def render_investigation_markdown(signal: DefectSignal, packet: dict[str, Any]) -> str:
    recommendation = packet["recommended_action"]
    evidence = packet["evidence"]
    recall = packet["visible_recall_cross_reference"]
    top_factors = packet["risk_factors"][:3]
    factors_text = "\n".join(
        f"- **{row['name'].replace('_', ' ').title()}**: {row['score']:.1f}/100 "
        f"(contribution {row['contribution']:.1f})"
        for row in top_factors
    )
    visible_recall = (
        f"Potentially covered by visible campaign {recall['campaign_number']} (similarity {recall['similarity']:.2f})."
        if recall["visible_recall_matched"]
        else f"No visible recall passed the configured match threshold (best similarity {recall['similarity']:.2f})."
    )
    odi_text = ", ".join(evidence["odi_numbers"][:12])
    if len(evidence["odi_numbers"]) > 12:
        odi_text += f", plus {len(evidence['odi_numbers']) - 12} more"

    deterministic_brief = EngineeringBriefRenderer().render_markdown(signal)
    return f"""# Engineering Investigation Card

**Vehicle:** {signal.vehicle.display_name}  
**Historical cutoff:** {signal.cutoff_date.isoformat()}  
**Emerging issue:** {signal.cluster.label}  
**Priority:** {signal.risk.level.value} — {signal.risk.final_score:.1f}/100  
**Alert gate:** {'PASSED' if signal.risk.alert else 'NOT PASSED'}

## Why it deserves attention

{factors_text}

## Evidence snapshot

- Supporting complaint records: **{evidence['supporting_complaints']}**
- Recent-window complaints: **{evidence['recent_window_count']}**
- Baseline-window complaints: **{evidence['baseline_window_count']}**
- Smoothed recent/baseline ratio: **{evidence['trend_ratio']:.2f}x**
- Associated source flags: crashes {evidence['crash_report_count']}, fires {evidence['fire_report_count']}, injury reports {evidence['injury_report_count']}, death reports {evidence['death_report_count']}
- ODI evidence: {odi_text or 'none'}

## Recall cross-reference at this cutoff

{visible_recall}

## Recommended workflow action

**{recommendation['title']}**  
{recommendation['summary']}

> RecallZero ranks evidence for human investigation. It does not determine that a safety defect exists and does not make recall decisions.

---

## Full deterministic engineering brief

{deterministic_brief}
"""


def alert_readiness() -> dict[str, Any]:
    url = os.getenv("RECALLZERO_DEMO_ALERT_WEBHOOK_URL", "").strip()
    provider = os.getenv("RECALLZERO_DEMO_ALERT_PROVIDER", "generic").strip().lower() or "generic"
    if provider not in {"generic", "slack", "teams"}:
        provider = "generic"
    return {
        "configured": bool(url),
        "provider": provider,
        "destination": "configured webhook" if url else "not configured",
        "note": (
            "Webhook delivery is optional. The investigation packet can always be downloaded without network access."
        ),
    }


def _alert_text(packet: dict[str, Any]) -> str:
    vehicle = packet["vehicle"]
    years = ",".join(str(value) for value in vehicle.get("model_years", []))
    priority = packet["priority"]
    evidence = packet["evidence"]
    action = packet["recommended_action"]
    return (
        f"RecallZero investigation: {vehicle.get('make')} {vehicle.get('model')} ({years})\n"
        f"{packet['signal']['issue']}\n"
        f"Risk {priority['risk_score']:.1f}/100 ({priority['level']}); "
        f"evidence {evidence['supporting_complaints']}; trend {evidence['trend_ratio']:.2f}x.\n"
        f"Action: {action['title']}.\n"
        "This is an investigation priority, not a defect or recall determination."
    )


def _send_alert_sync(packet: dict[str, Any]) -> dict[str, Any]:
    readiness = alert_readiness()
    url = os.getenv("RECALLZERO_DEMO_ALERT_WEBHOOK_URL", "").strip()
    if not readiness["configured"] or not url:
        raise DemoActionError("Demo alert webhook is not configured.")

    provider = readiness["provider"]
    text = _alert_text(packet)
    if provider in {"slack", "teams"}:
        body: dict[str, Any] = {"text": text}
    else:
        body = {
            "event": "recallzero.engineering_investigation",
            "summary": text,
            "packet": {key: value for key, value in packet.items() if key != "markdown"},
        }

    request = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "RecallZero-Demo/1.0"},
    )
    try:
        with urlopen(request, timeout=10.0) as response:
            status = getattr(response, "status", 200)
            preview = response.read(512).decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read(512).decode("utf-8", errors="replace")
        raise DemoActionError(f"Alert webhook returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise DemoActionError(f"Could not reach the configured alert webhook: {exc.reason}") from exc

    if not 200 <= int(status) < 300:
        raise DemoActionError(f"Alert webhook returned unexpected status {status}.")
    return {
        "sent": True,
        "provider": provider,
        "http_status": int(status),
        "response_preview": preview[:200],
    }


async def send_investigation_alert(packet: dict[str, Any]) -> dict[str, Any]:
    return await asyncio.to_thread(_send_alert_sync, packet)


def agent_readiness() -> dict[str, Any]:
    nat_path = shutil.which("nat")
    enabled = os.getenv("RECALLZERO_DEMO_ENABLE_AGENT", "false").strip().lower() in {"1", "true", "yes", "on"}
    config_path = Path(os.getenv("RECALLZERO_AGENT_CONFIG", "configs/aiq/recallzero_agent.yml"))
    return {
        "available": bool(nat_path and config_path.exists()),
        "execution_enabled": enabled,
        "nat_cli": nat_path,
        "config_path": str(config_path),
        "config_present": config_path.exists(),
        "agent_model": os.getenv("RECALLZERO_AGENT_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b"),
        "workflow": "tool_calling_agent",
        "tools": [
            "recallzero_fetch_vehicle_data",
            "recallzero_analyze_vehicle",
            "recallzero_run_backtest",
            "recallzero_get_evidence",
            "recallzero_engineering_brief",
        ],
        "note": (
            "The NVIDIA NeMo Agent Toolkit orchestrates RecallZero tools. Detector mathematics and evidence remain deterministic."
        ),
    }


def _run_agent_sync(prompt: str) -> dict[str, Any]:
    readiness = agent_readiness()
    if not readiness["execution_enabled"]:
        raise DemoActionError(
            "Agent execution is disabled. Set RECALLZERO_DEMO_ENABLE_AGENT=true after validating NAT on the GB10."
        )
    if not readiness["available"]:
        raise DemoActionError("NVIDIA NeMo Agent Toolkit is not ready: nat CLI or agent config is missing.")

    prompt = " ".join(prompt.split())
    if not prompt:
        raise DemoActionError("Agent prompt cannot be empty.")
    if len(prompt) > 800:
        raise DemoActionError("Agent prompt is limited to 800 characters for the live demo endpoint.")

    command = [
        str(readiness["nat_cli"]),
        "run",
        "--config_file",
        str(readiness["config_path"]),
        "--input",
        prompt,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as exc:
        raise DemoActionError("NVIDIA Investigator Agent exceeded the 180-second demo timeout.") from exc

    output = (completed.stdout or "") + (("\n" + completed.stderr) if completed.stderr else "")
    output = output.strip()
    if completed.returncode != 0:
        raise DemoActionError(
            f"NVIDIA Investigator Agent exited with code {completed.returncode}: {output[-3000:]}"
        )
    return {
        "completed": True,
        "workflow": readiness["workflow"],
        "agent_model": readiness["agent_model"],
        "tools": readiness["tools"],
        "prompt": prompt,
        "output": output[-20000:],
        "detector_math_changed": False,
    }


async def run_investigator_agent(prompt: str) -> dict[str, Any]:
    return await asyncio.to_thread(_run_agent_sync, prompt)
