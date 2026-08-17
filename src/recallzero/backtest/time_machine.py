from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from recallzero.config import RiskConfig
from recallzero.models import (
    BacktestCandidate,
    BacktestResult,
    BacktestSnapshot,
    Complaint,
    DefectSignal,
    FailureSignature,
    Recall,
    Vehicle,
)
from recallzero.pipeline import RecallZeroPipeline
from recallzero.utils import stable_id


class RecallTimeMachine:
    """Leakage-safe weekly replay against a known historical recall outcome."""

    def __init__(self, pipeline: RecallZeroPipeline, target_match_threshold: float = 0.45, top_candidate_count: int = 5):
        self.pipeline = pipeline
        self.target_match_threshold = target_match_threshold
        self.top_candidate_count = max(1, top_candidate_count)

    @staticmethod
    def _cutoff_schedule(start: date, official_recall_date: date) -> list[date]:
        final_cutoff = official_recall_date - timedelta(days=1)
        if start > final_cutoff:
            return []
        values: list[date] = []
        current = start
        while current <= final_cutoff:
            values.append(current)
            current += timedelta(days=7)
        if values[-1] != final_cutoff:
            values.append(final_cutoff)
        return values

    async def run(
        self,
        *,
        vehicle: Vehicle,
        complaints: Sequence[Complaint],
        recalls: Sequence[Recall],
        target_recall: Recall,
        official_recall_date: date,
        replay_start_date: date | None = None,
        alert_threshold: float | None = None,
        minimum_evidence: int | None = None,
        use_signature_cache: bool = True,
        save: bool = True,
    ) -> BacktestResult:
        target_campaign = target_recall.campaign_number
        pre_recall = sorted(
            (complaint for complaint in complaints if complaint.received_date < official_recall_date),
            key=lambda item: (item.received_date, item.odi_number),
        )
        post_recall_count = sum(1 for complaint in complaints if complaint.received_date >= official_recall_date)
        latest_used = max((complaint.received_date for complaint in pre_recall), default=None)
        if replay_start_date is None:
            one_year_before = official_recall_date - timedelta(days=365)
            earliest = min((complaint.received_date for complaint in pre_recall), default=one_year_before)
            replay_start_date = max(earliest, one_year_before)

        signatures: list[FailureSignature] = []
        if pre_recall:
            signatures = await self.pipeline.extract_signatures(vehicle, pre_recall, use_cache=use_signature_cache)
        signature_by_id = {item.complaint_id: item for item in signatures}
        complaint_by_id = {item.odi_number: item for item in pre_recall}

        risk_config: RiskConfig = self.pipeline.risk_config.model_copy(deep=True)
        if alert_threshold is not None:
            risk_config.alert_threshold = alert_threshold
        if minimum_evidence is not None:
            risk_config.minimum_evidence = minimum_evidence

        frozen_snapshots: list[BacktestSnapshot] = []
        candidate_matches: list[tuple[date, float, DefectSignal]] = []
        all_alerts: list[tuple[date, DefectSignal]] = []
        all_posthoc_candidates: list[tuple[date, float, DefectSignal]] = []
        degraded_snapshot_count = 0

        target_rows = [item for item in recalls if item.campaign_number == target_campaign]
        target_public_before_boundary = any(
            item.report_received_date is not None and item.report_received_date < official_recall_date
            for item in [target_recall, *target_rows]
        )
        detection_recalls = [item for item in recalls if item.campaign_number != target_campaign]

        for cutoff in self._cutoff_schedule(replay_start_date, official_recall_date):
            visible = [complaint for complaint in pre_recall if complaint.received_date <= cutoff]
            visible_signatures = [signature_by_id[item.odi_number] for item in visible if item.odi_number in signature_by_id]
            run = await self.pipeline.analyze_records(
                vehicle=vehicle,
                complaints=visible,
                recalls=detection_recalls,
                cutoff_date=cutoff,
                signatures=visible_signatures,
                risk_config=risk_config,
                save=False,
            )
            degraded = run.semantic_quality == "DEGRADED"
            if degraded:
                degraded_snapshot_count += 1
                alerts = tuple()
            else:
                alerts = tuple(signal for signal in run.signals if signal.risk.alert)

            ranked = list(run.signals[: self.top_candidate_count])
            candidate_rows: list[BacktestCandidate] = []
            for signal in ranked:
                member_signatures = [
                    signature_by_id[item_id]
                    for item_id in signal.cluster.member_ids
                    if item_id in signature_by_id
                ]
                member_complaints = [
                    complaint_by_id[item_id]
                    for item_id in signal.cluster.member_ids
                    if item_id in complaint_by_id
                ]
                # Post-hoc evaluation only. The detector run above was already frozen
                # without target recall text.
                details = self.pipeline.recall_matcher.score_target_details(
                    signal.cluster, member_signatures, target_recall, member_complaints
                )
                target_score = details["final"]
                all_posthoc_candidates.append((cutoff, target_score, signal))
                candidate_rows.append(
                    BacktestCandidate(
                        signal_id=signal.signal_id,
                        lineage_id=signal.lineage_id,
                        signal_scope=signal.signal_scope,
                        issue=signal.cluster.label,
                        failure_mechanism=signal.cluster.failure_mechanism,
                        consequence_family=signal.cluster.consequence_family,
                        evidence_count=signal.cluster.evidence_count,
                        risk_score=signal.risk.final_score,
                        alert=signal.risk.alert and not degraded,
                        distance_to_alert_threshold=round(max(0.0, risk_config.alert_threshold - signal.risk.final_score), 2),
                        posthoc_target_score=target_score,
                        posthoc_target_breakdown={key: float(value) for key, value in details.items()},
                    )
                )

            max_risk = max((signal.risk.final_score for signal in run.signals), default=0.0)
            max_signal = next((signal for signal in run.signals if signal.risk.final_score == max_risk), None)
            frozen_snapshots.append(
                BacktestSnapshot(
                    cutoff_date=cutoff,
                    complaint_count_visible=len(visible),
                    signal_count=len(run.signals),
                    alerts=alerts,
                    max_risk_score=max_risk,
                    max_risk_signal_id=max_signal.signal_id if max_signal else None,
                    distance_to_alert_threshold=round(max(0.0, risk_config.alert_threshold - max_risk), 2),
                    top_candidates=tuple(candidate_rows),
                )
            )

            for signal in alerts:
                all_alerts.append((cutoff, signal))
                member_signatures = [
                    signature_by_id[item_id]
                    for item_id in signal.cluster.member_ids
                    if item_id in signature_by_id
                ]
                member_complaints = [
                    complaint_by_id[item_id]
                    for item_id in signal.cluster.member_ids
                    if item_id in complaint_by_id
                ]
                target_score = self.pipeline.recall_matcher.score_target(
                    signal.cluster, member_signatures, target_recall, member_complaints
                )
                if target_score >= self.target_match_threshold:
                    candidate_matches.append((cutoff, target_score, signal))

        candidate_matches.sort(key=lambda item: (item[0], -item[1], -item[2].risk.final_score))
        all_alerts.sort(key=lambda item: (item[0], -item[1].risk.final_score))
        target_like_candidates = sorted(
            (item for item in all_posthoc_candidates if item[1] >= self.target_match_threshold),
            key=lambda item: (item[0], -item[1], -item[2].risk.final_score),
        )
        all_posthoc_candidates.sort(key=lambda item: (-item[1], item[0], -item[2].risk.final_score))

        first_date = candidate_matches[0][0] if candidate_matches else None
        match_score = candidate_matches[0][1] if candidate_matches else None
        matched_signal_id = candidate_matches[0][2].signal_id if candidate_matches else None
        first_any_alert_date = all_alerts[0][0] if all_alerts else None
        first_any_alert_signal_id = all_alerts[0][1].signal_id if all_alerts else None
        earliest_target_like = target_like_candidates[0] if target_like_candidates else None
        lead_time = (official_recall_date - first_date).days if first_date else None
        best_posthoc = all_posthoc_candidates[0] if all_posthoc_candidates else None

        if first_date:
            status = "EARLY_SIGNAL_DETECTED"
        elif all_alerts:
            status = "EARLY_ALERT_TARGET_UNMATCHED"
        else:
            status = "NO_EARLY_SIGNAL"

        checks = {
            "complaints_strictly_before_official_recall": all(
                complaint.received_date < official_recall_date for complaint in pre_recall
            ),
            "snapshots_strictly_before_official_recall": all(
                snapshot.cutoff_date < official_recall_date for snapshot in frozen_snapshots
            ),
            "snapshot_evidence_strictly_before_official_recall": all(
                evidence.received_date < official_recall_date
                for snapshot in frozen_snapshots
                for signal in snapshot.alerts
                for evidence in signal.evidence
            ),
            "target_recall_not_used_during_detection": not target_public_before_boundary,
            "post_recall_complaints_excluded": post_recall_count == len(complaints) - len(pre_recall),
        }
        warnings: list[str] = []
        if not pre_recall:
            warnings.append("No complaints were available before the official recall date.")
        if degraded_snapshot_count:
            warnings.append(
                f"Withheld alerts from {degraded_snapshot_count} replay snapshot(s) because semantic/clustering quality was DEGRADED."
            )
        if all_alerts and not candidate_matches:
            warnings.append(
                "One or more quality-qualified pre-recall alerts passed the risk gate, but none passed the configured post-hoc target-recall match threshold."
            )
        elif not all_alerts:
            warnings.append("No quality-qualified pre-recall alert passed the configured risk gate.")
        if best_posthoc and best_posthoc[1] >= self.target_match_threshold and not candidate_matches:
            warnings.append(
                "A frozen pre-recall top candidate matched the target recall post-hoc but did not pass the risk gate; use the snapshot diagnostics to distinguish representation/risk calibration from target matching."
            )
        if not all(checks.values()):
            warnings.append("One or more anti-leakage checks failed; do not report a lead-time result.")
            status = "INVALID_BACKTEST"
            first_date = None
            match_score = None
            matched_signal_id = None
            first_any_alert_date = None
            first_any_alert_signal_id = None
            earliest_target_like = None
            lead_time = None

        backtest_id = stable_id(
            "bt",
            vehicle.slug,
            target_campaign,
            official_recall_date,
            risk_config.alert_threshold,
            risk_config.minimum_evidence,
        )
        result = BacktestResult(
            backtest_id=backtest_id,
            vehicle=vehicle,
            target_campaign_number=target_campaign,
            official_recall_date=official_recall_date,
            first_matching_alert_date=first_date,
            lead_time_days=lead_time,
            matched_signal_id=matched_signal_id,
            target_match_score=match_score,
            first_any_alert_date=first_any_alert_date,
            first_any_alert_signal_id=first_any_alert_signal_id,
            earliest_target_like_candidate_date=earliest_target_like[0] if earliest_target_like else None,
            earliest_target_like_candidate_signal_id=earliest_target_like[2].signal_id if earliest_target_like else None,
            earliest_target_like_candidate_score=earliest_target_like[1] if earliest_target_like else None,
            first_qualified_alert_date=first_date,
            first_qualified_alert_signal_id=matched_signal_id,
            alert_snapshot_count=sum(1 for snapshot in frozen_snapshots if snapshot.alerts),
            max_pre_alert_target_score=best_posthoc[1] if best_posthoc else None,
            max_pre_alert_target_date=best_posthoc[0] if best_posthoc else None,
            max_pre_alert_signal_id=best_posthoc[2].signal_id if best_posthoc else None,
            status=status,
            snapshots=tuple(frozen_snapshots),
            complaints_considered=len(pre_recall),
            latest_complaint_date_used=latest_used,
            anti_leakage_checks=checks,
            warnings=tuple(warnings),
        )
        if save:
            self.pipeline.repository.save_backtest(result)
        return result
