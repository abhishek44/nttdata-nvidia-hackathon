from __future__ import annotations

from dataclasses import dataclass

from recallzero.models import DefectSignal


@dataclass(slots=True)
class CriticFinding:
    passed: bool
    checks: dict[str, bool]
    notes: tuple[str, ...]


class EvidenceCritic:
    """Deterministic consistency checks for claims shown in an engineering brief."""

    def audit(self, signal: DefectSignal) -> CriticFinding:
        evidence_ids = {item.complaint_id for item in signal.evidence}
        cluster_ids = set(signal.cluster.member_ids)
        checks = {
            "cluster_count_matches_evidence": signal.cluster.evidence_count == len(evidence_ids),
            "all_cluster_members_have_evidence": cluster_ids == evidence_ids,
            "recent_count_not_above_total": signal.trend.recent_count <= signal.cluster.evidence_count,
            "risk_contributions_sum": abs(
                sum(item.contribution for item in signal.risk.factors) - signal.risk.final_score
            ) < 0.11,
            "recall_claim_has_campaign": (not signal.recall_match.matched)
            or bool(signal.recall_match.campaign_number),
        }
        notes: list[str] = []
        if any(item.crash for item in signal.evidence):
            notes.append(
                "Crash flags are presented as associated reports; the evidence package does not establish defect causation."
            )
        if any(item.fire for item in signal.evidence):
            notes.append(
                "Fire flags are presented as associated reports; the evidence package does not establish root cause."
            )
        if signal.cluster.is_noise:
            notes.append("This is a DBSCAN noise/singleton cluster and should not be treated as a stable pattern.")
        return CriticFinding(passed=all(checks.values()), checks=checks, notes=tuple(notes))


class EngineeringBriefRenderer:
    def __init__(self, critic: EvidenceCritic | None = None):
        self.critic = critic or EvidenceCritic()

    def render_markdown(self, signal: DefectSignal) -> str:
        audit = self.critic.audit(signal)
        recall_text = (
            f"Potentially covered by campaign {signal.recall_match.campaign_number} "
            f"(similarity {signal.recall_match.score:.2f})."
            if signal.recall_match.matched
            else f"No visible recall passed the configured match threshold (best similarity {signal.recall_match.score:.2f})."
        )
        ids = ", ".join(item.complaint_id for item in signal.evidence[:12])
        if len(signal.evidence) > 12:
            ids += f", plus {len(signal.evidence) - 12} more"
        critic_text = "PASS" if audit.passed else "REVIEW REQUIRED"
        notes = "\n".join(f"- {note}" for note in audit.notes) or "- No additional critic notes."
        return f"""# RecallZero Engineering Investigation Brief

**Vehicle:** {signal.vehicle.display_name}  
**Cutoff date:** {signal.cutoff_date.isoformat()}  
**Emerging issue:** {signal.cluster.label}  
**Priority:** {signal.risk.level.value} — {signal.risk.final_score:.1f}/100  
**Alert gate:** {'PASSED' if signal.risk.alert else 'NOT PASSED'}

## Evidence

- Supporting complaint records: {signal.cluster.evidence_count}
- Complaints in recent window: {signal.trend.recent_count}
- Baseline complaints: {signal.trend.baseline_count}
- Smoothed recent/baseline ratio: {signal.trend.trend_ratio:.2f}x
- Consecutive recent weeks with evidence: {signal.trend.persistence_weeks}
- Complaint IDs: {ids or 'None'}

## Recall cross-reference

{recall_text}

## Assessment

{signal.risk.rationale}

This output ranks a pattern for engineering investigation. It does not declare that a safety defect exists.

## Evidence critic

**Status:** {critic_text}

{notes}
"""
