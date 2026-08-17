from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from recallzero.config import RiskConfig
from recallzero.models import (
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

    def __init__(self, pipeline: RecallZeroPipeline, target_match_threshold: float = 0.20):
        self.pipeline = pipeline
        self.target_match_threshold = target_match_threshold

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
            signatures = await self.pipeline.extract_signatures(
                vehicle, pre_recall, use_cache=use_signature_cache
            )
        signature_by_id = {item.complaint_id: item for item in signatures}

        risk_config: RiskConfig = self.pipeline.risk_config.model_copy(deep=True)
        if alert_threshold is not None:
            risk_config.alert_threshold = alert_threshold
        if minimum_evidence is not None:
            risk_config.minimum_evidence = minimum_evidence

        frozen_snapshots: list[BacktestSnapshot] = []
        candidate_matches: list[tuple[date, float, DefectSignal]] = []

        # The target campaign is explicitly removed from the detector's recall set. If it
        # was already public before the declared boundary, the experiment is marked invalid
        # rather than allowing the future outcome to suppress the recall-gap signal.
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
            alerts = tuple(signal for signal in run.signals if signal.risk.alert)
            frozen_snapshots.append(
                BacktestSnapshot(
                    cutoff_date=cutoff,
                    complaint_count_visible=len(visible),
                    signal_count=len(run.signals),
                    alerts=alerts,
                )
            )
            for signal in alerts:
                member_signatures = [
                    signature_by_id[item_id]
                    for item_id in signal.cluster.member_ids
                    if item_id in signature_by_id
                ]
                # Post-hoc evaluation only: target recall text is introduced after the signal has been frozen.
                target_score = self.pipeline.recall_matcher.score_target(signal.cluster, member_signatures, target_recall)
                if target_score >= self.target_match_threshold:
                    candidate_matches.append((cutoff, target_score, signal))

        candidate_matches.sort(key=lambda item: (item[0], -item[1], -item[2].risk.final_score))
        first_date = candidate_matches[0][0] if candidate_matches else None
        match_score = candidate_matches[0][1] if candidate_matches else None
        matched_signal_id = candidate_matches[0][2].signal_id if candidate_matches else None
        lead_time = (official_recall_date - first_date).days if first_date else None
        status = "EARLY_SIGNAL_DETECTED" if first_date else "NO_EARLY_SIGNAL"

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
        if not candidate_matches:
            warnings.append(
                "No alert both passed the configured risk gate and matched the target recall in post-hoc evaluation."
            )
        if not all(checks.values()):
            warnings.append("One or more anti-leakage checks failed; do not report a lead-time result.")
            status = "INVALID_BACKTEST"
            first_date = None
            match_score = None
            matched_signal_id = None
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
