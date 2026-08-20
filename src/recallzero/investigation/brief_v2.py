from __future__ import annotations

from recallzero.investigation.brief import EngineeringBriefRenderer
from recallzero.models import ExtractionMethod
from recallzero.models.v2 import SignalV2

_LIMITATION = (
    "**Investigative signal - not a confirmed defect.** Complaint data is self-reported, "
    "unverified, and may include duplication. Causation is not established. This output ranks a "
    "pattern for engineering investigation only."
)


class EngineeringBriefRendererV2:
    """Renders a Detector v2 engineering brief with the six-factor contribution
    breakdown, the affected-coverage section, precise recall wording, and an explicit
    investigative-limitation banner.
    """

    def __init__(self) -> None:
        self._v1 = EngineeringBriefRenderer()

    def render_markdown(self, signal_v2: SignalV2) -> str:
        signal = signal_v2.signal
        coverage = signal_v2.coverage
        risk = signal_v2.risk
        audit = self._v1.critic.audit(signal)

        factor_rows = "\n".join(
            f"| {factor.name} | {factor.score:.1f} | {factor.weight:.2f} | {factor.contribution:.1f} |"
            for factor in risk.factors
        )
        reasons = "\n".join(f"- **{factor.name}**: {factor.explanation}" for factor in risk.factors)
        ids = ", ".join(item.complaint_id for item in signal.evidence[:12])
        if len(signal.evidence) > 12:
            ids += f", plus {len(signal.evidence) - 12} more"
        nim_count = sum(1 for item in signal.evidence if item.signature.extraction_method == ExtractionMethod.NIM)
        recall_text = (
            f"Potentially covered by campaign {signal.recall_match.campaign_number} "
            f"(similarity {signal.recall_match.score:.2f})."
            if signal.recall_match.matched
            else (
                f"No matching recall campaign visible to the detector at "
                f"{signal.cutoff_date.isoformat()} (best similarity {signal.recall_match.score:.2f}). "
                "This does not prove no recall exists."
            )
        )
        return f"""# RecallZero Detector v2 - Engineering Investigation Brief

> {_LIMITATION}

**Vehicle:** {signal.vehicle.display_name}
**Analysis cutoff:** {signal.cutoff_date.isoformat()}
**Emerging issue:** {signal.cluster.label}
**Signal scope:** {signal.signal_scope}
**Failure mechanism:** {signal.cluster.failure_mechanism}
**Consequence family:** {signal.cluster.consequence_family}

## Risk score (Detector v2, six factors)

**Overall risk: {risk.final_score:.1f}/100 - {risk.level.value}** (alert gate {'PASSED' if risk.alert else 'NOT PASSED'} at {risk.threshold:.0f})

| Factor | Score (0-100) | Weight | Contribution |
| --- | --- | --- | --- |
{factor_rows}

**Why:**
{reasons}

## Affected coverage

- Distinct model years: {coverage.distinct_model_years} (span {coverage.model_year_span})
- Distinct VIN prefixes: {coverage.distinct_vin_prefixes} ({coverage.vin_evidence_ratio:.0%} of complaints)
  - *11-character prefix (WMI+VDS+check+year+plant) - a conservative proxy for distinct vehicles, not an exact unique-VIN count.*
- Distinct vehicle models sharing this issue: {coverage.distinct_vehicles}

{coverage.explanation}

## Trend and persistence

- Recent {signal.trend.recent_window_days}-day complaints: {signal.trend.recent_count}
- Baseline {signal.trend.baseline_window_days}-day complaints: {signal.trend.baseline_count}
- Smoothed recent/baseline ratio: {signal.trend.trend_ratio:.2f}x
- Active weeks (recent 4-week horizon): {signal.trend.active_weeks_recent_4}
- Max consecutive active weeks (8-week horizon): {signal.trend.max_consecutive_weeks_recent_8}

## Evidence

- Supporting complaint records: {signal.cluster.evidence_count}
- Complaint IDs: {ids or 'None'}
- NIM-extracted signatures in evidence: {nim_count}/{len(signal.evidence)}

## Recall cross-reference

{recall_text}

## Evidence critic

**Status:** {'PASS' if audit.passed else 'REVIEW REQUIRED'}

{_chr_join(audit.notes)}

## Recommended engineering actions

- Review warranty and field-return data for this component.
- Check diagnostic trouble codes and production ranges for the affected model years.
- Compare VIN production ranges; inspect failed parts and component revisions.
- Determine whether an investigation should be expanded.
"""


def _chr_join(notes) -> str:
    items = list(notes)
    if not items:
        return "- No additional critic notes."
    return "\n".join(f"- {note}" for note in items)