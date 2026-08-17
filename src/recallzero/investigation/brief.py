from __future__ import annotations

from dataclasses import dataclass

from recallzero.intelligence.clustering import canonical_family
from recallzero.models import DefectSignal, ExtractionMethod


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
        component_aligned = 0
        component_checkable = 0
        for item in signal.evidence:
            families = {canonical_family(component) for component in item.components}
            families.discard("UNKNOWN")
            if families:
                component_checkable += 1
                expected_families = set(signal.cluster.source_systems) or {signal.cluster.system}
                if families & expected_families:
                    component_aligned += 1
        component_alignment_ratio = component_aligned / max(1, component_checkable)

        checks = {
            "cluster_count_matches_evidence": signal.cluster.evidence_count == len(evidence_ids),
            "all_cluster_members_have_evidence": cluster_ids == evidence_ids,
            "recent_count_not_above_total": signal.trend.recent_count <= signal.cluster.evidence_count,
            "risk_contributions_sum": abs(
                sum(item.contribution for item in signal.risk.factors) - signal.risk.final_score
            ) < 0.11,
            "recall_claim_has_campaign": (not signal.recall_match.matched)
            or bool(signal.recall_match.campaign_number),
            "component_family_alignment": component_checkable == 0 or component_alignment_ratio >= 0.50,
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
        if component_checkable and component_alignment_ratio < 0.75:
            notes.append(
                f"Only {component_alignment_ratio:.0%} of evidence records with NHTSA component labels align with "
                f"the signal source systems {', '.join(signal.cluster.source_systems) or signal.cluster.system}; "
                "review semantic grouping before escalation."
            )
        if "MALFUNCTION" in signal.cluster.failure_mode.upper() and signal.cluster.evidence_count >= 10:
            notes.append(
                "The cluster failure mode is generic (MALFUNCTION); inspect representative ODI narratives before treating it as a specific failure signature."
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
        nim_count = sum(
            1 for item in signal.evidence if item.signature.extraction_method == ExtractionMethod.NIM
        )
        heuristic_count = len(signal.evidence) - nim_count
        heuristic_ratio = heuristic_count / max(1, len(signal.evidence))
        if heuristic_ratio > 0.20:
            semantic_quality = (
                f"DEGRADED — {heuristic_count}/{len(signal.evidence)} supporting signatures used heuristic fallback. "
                "Treat the semantic cluster label and alert interpretation as unvalidated until NIM extraction succeeds."
            )
        else:
            semantic_quality = (
                f"ACCEPTABLE — {nim_count}/{len(signal.evidence)} supporting signatures were extracted with NIM."
            )
        return f"""# RecallZero Engineering Investigation Brief

**Vehicle:** {signal.vehicle.display_name}  
**Cutoff date:** {signal.cutoff_date.isoformat()}  
**Emerging issue:** {signal.cluster.label}  
**Signal scope:** {signal.signal_scope}  
**Failure mechanism:** {signal.cluster.failure_mechanism}  
**Consequence family:** {signal.cluster.consequence_family}  
**Priority:** {signal.risk.level.value} — {signal.risk.final_score:.1f}/100  
**Alert gate:** {'PASSED' if signal.risk.alert else 'NOT PASSED'}

## Evidence

- Supporting complaint records: {signal.cluster.evidence_count}
- Complaints in recent window: {signal.trend.recent_count}
- Baseline complaints: {signal.trend.baseline_count}
- Smoothed recent/baseline ratio: {signal.trend.trend_ratio:.2f}x
- Active weeks in the recent 4-week horizon: {signal.trend.active_weeks_recent_4}
- Maximum consecutive active weeks in the recent 8-week horizon: {signal.trend.max_consecutive_weeks_recent_8}
- Complaint IDs: {ids or 'None'}

## Semantic extraction quality

{semantic_quality}

## Recall cross-reference

{recall_text}

## Assessment

{signal.risk.rationale}

This output ranks a pattern for engineering investigation. It does not declare that a safety defect exists.

## Evidence critic

**Status:** {critic_text}

{notes}
"""
