from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime

from recallzero.analytics import RiskEngine, SeverityEngine, TrendEngine
from recallzero.config import RiskConfig, Settings, get_settings
from recallzero.data import FileRepository, NHTSAClient
from recallzero.intelligence import (
    HeuristicFailureExtractor,
    HybridEmbedder,
    HybridFailureExtractor,
    NIMClient,
    NIMEmbedder,
    NIMFailureExtractor,
    TFIDFEmbedder,
    extraction_method_counts,
)
from recallzero.intelligence.clustering import ComplaintClusterer
from recallzero.models import (
    AnalysisRun,
    Complaint,
    DefectSignal,
    EmbeddingMethod,
    EvidenceItem,
    FailureSignature,
    Recall,
    Vehicle,
)
from recallzero.recall import RecallMatcher
from recallzero.utils import excerpt, stable_id

logger = logging.getLogger(__name__)


class RecallZeroPipeline:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: FileRepository,
        nhtsa: NHTSAClient,
        extractor: HybridFailureExtractor,
        clusterer: ComplaintClusterer,
        trend_engine: TrendEngine,
        severity_engine: SeverityEngine,
        recall_matcher: RecallMatcher,
        risk_config: RiskConfig,
    ):
        self.settings = settings
        self.repository = repository
        self.nhtsa = nhtsa
        self.extractor = extractor
        self.clusterer = clusterer
        self.trend_engine = trend_engine
        self.severity_engine = severity_engine
        self.recall_matcher = recall_matcher
        self.risk_config = risk_config

    async def ingest(self, vehicle: Vehicle, *, refresh: bool = False) -> tuple[list[Complaint], list[Recall]]:
        complaints = None if refresh else self.repository.load_complaints(vehicle)
        recalls = None if refresh else self.repository.load_recalls(vehicle)

        if complaints is None:
            complaints, raw_complaints = await self.nhtsa.fetch_complaints(vehicle)
            self.repository.save_raw(vehicle, "complaints", raw_complaints)
            self.repository.save_complaints(vehicle, complaints)
        if recalls is None:
            recalls, raw_recalls = await self.nhtsa.fetch_recalls(vehicle)
            self.repository.save_raw(vehicle, "recalls", raw_recalls)
            self.repository.save_recalls(vehicle, recalls)
        return complaints, recalls

    async def extract_signatures(
        self,
        vehicle: Vehicle,
        complaints: Sequence[Complaint],
        *,
        use_cache: bool = True,
    ) -> list[FailureSignature]:
        # Persist successful extraction progress in small batches. A long hosted-NIM
        # run can therefore resume after interruption or rate limiting without
        # repeating already-completed ODI records.
        cache = self.repository.load_signatures(vehicle) if use_cache else {}
        merged = dict(cache)
        output: list[FailureSignature] = []
        batch_size = max(1, self.settings.signature_batch_size)
        def _checkpoint(signature: FailureSignature) -> None:
            merged[signature.complaint_id] = signature
            # Successful NIM work is persisted immediately. If a later request
            # exhausts its 429/5xx retry budget, the next run resumes from here.
            self.repository.save_signatures(vehicle, merged.values())

        for start in range(0, len(complaints), batch_size):
            batch = list(complaints[start : start + batch_size])
            batch_signatures = await self.extractor.extract_many(
                batch, cached=merged, on_result=_checkpoint
            )
            output.extend(batch_signatures)
            merged.update({signature.complaint_id: signature for signature in batch_signatures})
            self.repository.save_signatures(vehicle, merged.values())
            logger.info(
                "Failure-signature cache checkpoint: %s/%s complaints processed (%s cached signatures)",
                min(start + len(batch), len(complaints)),
                len(complaints),
                len(merged),
            )
        return output

    @staticmethod
    def _visible_recalls(recalls: Sequence[Recall], cutoff_date: date) -> tuple[list[Recall], int]:
        visible: list[Recall] = []
        unknown_date = 0
        for recall in recalls:
            if recall.report_received_date is None:
                unknown_date += 1
                continue
            if recall.report_received_date <= cutoff_date:
                visible.append(recall)
        return visible, unknown_date

    async def analyze_records(
        self,
        *,
        vehicle: Vehicle,
        complaints: Sequence[Complaint],
        recalls: Sequence[Recall],
        cutoff_date: date,
        signatures: Sequence[FailureSignature] | None = None,
        risk_config: RiskConfig | None = None,
        save: bool = False,
    ) -> AnalysisRun:
        visible_complaints = sorted(
            (complaint for complaint in complaints if complaint.received_date <= cutoff_date),
            key=lambda item: (item.received_date, item.odi_number),
        )
        if signatures is None:
            signatures = await self.extract_signatures(vehicle, visible_complaints)
        signatures_by_id = {signature.complaint_id: signature for signature in signatures}
        visible_signatures = [signatures_by_id[item.odi_number] for item in visible_complaints if item.odi_number in signatures_by_id]
        complaints_by_id = {complaint.odi_number: complaint for complaint in visible_complaints}

        clusters, embedding, clustering_diagnostics = await self.clusterer.cluster_with_diagnostics(
            visible_complaints, visible_signatures
        )
        visible_recalls, unknown_recall_dates = self._visible_recalls(recalls, cutoff_date)
        risk_engine = RiskEngine(risk_config or self.risk_config)

        signals: list[DefectSignal] = []
        for cluster in clusters:
            member_complaints = [complaints_by_id[item_id] for item_id in cluster.member_ids if item_id in complaints_by_id]
            member_signatures = [signatures_by_id[item_id] for item_id in cluster.member_ids if item_id in signatures_by_id]
            trend = self.trend_engine.calculate(member_complaints, cutoff_date)
            severity = self.severity_engine.calculate(member_complaints, member_signatures)
            recall_match = self.recall_matcher.find_best_match(
                cluster, member_signatures, visible_recalls, member_complaints
            )
            risk = risk_engine.calculate(
                severity=severity,
                trend=trend,
                recall_match=recall_match,
                evidence_count=cluster.evidence_count,
            )
            evidence = tuple(
                EvidenceItem(
                    complaint_id=complaint.odi_number,
                    received_date=complaint.received_date,
                    components=complaint.components,
                    narrative_excerpt=excerpt(complaint.narrative),
                    crash=complaint.crash,
                    fire=complaint.fire,
                    injuries=complaint.injuries,
                    deaths=complaint.deaths,
                    signature=signatures_by_id[complaint.odi_number],
                    validated_severity_indicators=tuple(
                        severity.evidence_by_complaint.get(complaint.odi_number, {}).keys()
                    ),
                    severity_evidence=severity.evidence_by_complaint.get(complaint.odi_number, {}),
                )
                for complaint in sorted(member_complaints, key=lambda item: item.received_date, reverse=True)
            )
            signal_id = stable_id("sig", vehicle.slug, cutoff_date, cluster.cluster_id)
            signals.append(
                DefectSignal(
                    signal_id=signal_id,
                    vehicle=vehicle,
                    cutoff_date=cutoff_date,
                    cluster=cluster,
                    trend=trend,
                    recall_match=recall_match,
                    risk=risk,
                    evidence=evidence,
                )
            )

        signals.sort(
            key=lambda item: (
                not item.risk.alert,
                -item.risk.final_score,
                -item.cluster.evidence_count,
                item.cluster.label,
            )
        )
        warnings: list[str] = []
        if not visible_complaints:
            warnings.append("No complaint records were visible at the requested cutoff date.")
        if unknown_recall_dates:
            warnings.append(f"Excluded {unknown_recall_dates} recall record(s) with no usable report date from cutoff logic.")
        methods = extraction_method_counts(visible_signatures)
        heuristic_count = methods.get("heuristic", 0)
        fallback_ratio = heuristic_count / max(1, len(visible_signatures))
        semantic_quality = "HEURISTIC_ONLY" if self.extractor.nim is None else "ACCEPTABLE"
        if heuristic_count:
            warnings.append(
                f"{heuristic_count} signature(s) used the deterministic heuristic path. Review these before relying on semantic labels."
            )
            if self.extractor.nim is not None and fallback_ratio > 0.20:
                semantic_quality = "DEGRADED"
                warnings.append(
                    "DEGRADED SEMANTIC QUALITY: "
                    f"{fallback_ratio:.0%} of visible signatures used heuristic fallback. "
                    "Treat cluster labels and engineering alerts as unvalidated until NIM extraction succeeds."
                )
            elif self.extractor.nim is not None and fallback_ratio > 0:
                semantic_quality = "MIXED"

        if clustering_diagnostics.get("suspicious_single_cluster"):
            semantic_quality = "DEGRADED"
            warnings.append(
                "CLUSTER QUALITY WARNING: all visible complaints collapsed into one semantic cluster. "
                "Do not treat the cluster label as a validated failure pattern."
            )
        elif clustering_diagnostics.get("suspicious_dominant_cluster"):
            warnings.append(
                "CLUSTER QUALITY WARNING: the largest semantic cluster contains "
                f"{float(clustering_diagnostics.get('largest_cluster_share', 0.0)):.0%} of visible complaints. "
                "Review component-family and distance diagnostics before relying on the label."
            )

        run_id = stable_id("run", vehicle.slug, cutoff_date, datetime.now(UTC).isoformat(), len(visible_complaints))
        run = AnalysisRun(
            run_id=run_id,
            vehicle=vehicle,
            cutoff_date=cutoff_date,
            complaint_count=len(visible_complaints),
            recall_count_visible=len(visible_recalls),
            signature_count=len(visible_signatures),
            cluster_count=len(clusters),
            signals=tuple(signals),
            extraction_method_counts=methods,
            embedding_method=embedding.method if clusters or visible_complaints else EmbeddingMethod.TFIDF,
            semantic_quality=semantic_quality,
            clustering_diagnostics=clustering_diagnostics,
            warnings=tuple(warnings),
        )
        if save:
            self.repository.save_analysis_run(run)
        return run

    async def analyze_vehicle(
        self,
        vehicle: Vehicle,
        *,
        cutoff_date: date | None = None,
        refresh: bool = False,
        max_complaints: int | None = None,
    ) -> AnalysisRun:
        complaints, recalls = await self.ingest(vehicle, refresh=refresh)
        cutoff = cutoff_date or date.today()
        visible = [item for item in complaints if item.received_date <= cutoff]
        if max_complaints is not None and len(visible) > max_complaints:
            visible = sorted(visible, key=lambda item: item.received_date)[-max_complaints:]
        signatures = await self.extract_signatures(vehicle, visible, use_cache=not refresh)
        return await self.analyze_records(
            vehicle=vehicle,
            complaints=visible,
            recalls=recalls,
            cutoff_date=cutoff,
            signatures=signatures,
            save=True,
        )


def build_pipeline(
    settings: Settings | None = None,
    *,
    use_nim: bool | None = None,
    risk_config: RiskConfig | None = None,
    nhtsa_client: NHTSAClient | None = None,
) -> RecallZeroPipeline:
    settings = settings or get_settings()
    settings.ensure_directories()
    nim_enabled = settings.use_nim if use_nim is None else use_nim
    chat_client = NIMClient(
        api_key=settings.nvidia_api_key,
        base_url=settings.nim_base_url,
        timeout_seconds=settings.request_timeout_seconds,
        max_retries=settings.max_retries,
        retry_base_delay_seconds=settings.retry_base_delay_seconds,
        retry_max_delay_seconds=settings.retry_max_delay_seconds,
    )
    embedding_client = NIMClient(
        api_key=settings.nvidia_api_key,
        base_url=settings.embedding_base_url or settings.nim_base_url,
        timeout_seconds=settings.request_timeout_seconds,
        max_retries=settings.max_retries,
        retry_base_delay_seconds=settings.retry_base_delay_seconds,
        retry_max_delay_seconds=settings.retry_max_delay_seconds,
    )
    chat_available = nim_enabled and chat_client.configured
    embedding_available = nim_enabled and embedding_client.configured
    if nim_enabled and not chat_available:
        logger.warning("NIM extraction was requested but no credentialed hosted endpoint or local chat endpoint is configured; using heuristic extraction.")
    if nim_enabled and not embedding_available:
        logger.warning("NIM embeddings were requested but no credentialed hosted endpoint or local embedding endpoint is configured; using TF-IDF.")

    heuristic = HeuristicFailureExtractor()
    nim_extractor = NIMFailureExtractor(chat_client, settings.llm_model) if chat_available else None
    extractor = HybridFailureExtractor(
        heuristic=heuristic,
        nim=nim_extractor,
        concurrency=settings.llm_concurrency,
        fallback_on_error=True,
        fallback_on_transient_error=settings.transient_nim_fallback,
    )
    nim_embedder = NIMEmbedder(embedding_client, settings.embedding_model) if embedding_available else None
    embedder = HybridEmbedder(
        nim=nim_embedder,
        fallback=TFIDFEmbedder(),
        fallback_on_transient_error=settings.transient_nim_fallback,
    )
    clusterer = ComplaintClusterer(
        embedder=embedder,
        eps=settings.clustering.eps,
        min_samples=settings.clustering.min_samples,
        max_representatives=settings.clustering.max_representatives,
        hierarchical=settings.clustering.hierarchical,
        diagnostics_sample_size=settings.clustering.diagnostics_sample_size,
        suspicious_cluster_share=settings.clustering.suspicious_cluster_share,
        taxonomy_grouping=settings.clustering.taxonomy_grouping,
        refine_max_distance=settings.clustering.refine_max_distance,
    )
    trend_engine = TrendEngine(
        recent_window_days=settings.trend.recent_window_days,
        baseline_window_days=settings.trend.baseline_window_days,
        persistence_weeks_to_full_score=settings.trend.persistence_weeks_to_full_score,
    )
    return RecallZeroPipeline(
        settings=settings,
        repository=FileRepository(settings.data_dir),
        nhtsa=nhtsa_client
        or NHTSAClient(
            base_url=settings.nhtsa_base_url,
            timeout_seconds=settings.request_timeout_seconds,
            max_retries=settings.max_retries,
        ),
        extractor=extractor,
        clusterer=clusterer,
        trend_engine=trend_engine,
        severity_engine=SeverityEngine(),
        recall_matcher=RecallMatcher(),
        risk_config=risk_config or settings.risk_config(),
    )
