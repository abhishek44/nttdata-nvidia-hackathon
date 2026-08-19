from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

from recallzero.benchmark import BenchmarkCandidateRecord, BenchmarkRunResult, load_candidates
from recallzero.config import Settings
from recallzero.data.repository import FileRepository
from recallzero.intelligence.embeddings import NIMEmbedder
from recallzero.intelligence.nim_client import NIMClient
from recallzero.models import ComplaintCluster, EmbeddingMethod
from recallzero.recall.matcher import RecallMatcher
from recallzero.recall.target_attributor import TargetAttributor

CandidateRole = Literal["alert", "top_target", "alert_and_top_target"]


class AttributionExperimentCandidate(BaseModel):
    case_id: str
    name: str
    campaign_number: str
    cutoff_date: date
    signal_id: str
    lineage_id: str
    candidate_role: CandidateRole
    alert: bool
    issue: str
    signal_scope: str
    risk_score: float
    evidence_count: int
    component_compatible: float
    failure_mechanism_score: float
    consequence_family_score: float
    component_score: float
    subsystem_score: float
    consequence_score: float
    baseline_semantic_lexical: float
    embedding_similarity: float
    persisted_baseline_target_score: float | None = None
    baseline_recomputed_delta: float | None = None
    baseline_target_score: float
    experimental_target_score: float
    target_score_delta: float
    baseline_qualifies: bool
    experimental_qualifies: bool
    component_gate_closed: bool


class AttributionExperimentCase(BaseModel):
    case_id: str
    name: str
    campaign_number: str
    detector_status: str
    candidate_count: int
    alert_candidate_count: int
    top_target_candidate_count: int
    baseline_qualified_alert_count: int
    experimental_qualified_alert_count: int
    best_baseline_alert_score: float | None = None
    best_experimental_alert_score: float | None = None
    best_baseline_target_score: float | None = None
    best_experimental_target_score: float | None = None
    candidates: tuple[AttributionExperimentCandidate, ...]


class AttributionExperimentResult(BaseModel):
    experiment_id: str = "target-attribution-experiment-a"
    scope: str = "development_diagnostic_only"
    source_benchmark_run_id: str
    source_freeze_id: str
    source_freeze_verified: bool
    source_lock_verified: bool
    target_match_threshold: float = 0.45
    semantic_weight: float = 0.10
    baseline_semantic_method: str = "tfidf_cosine"
    experimental_semantic_method: str = "nim_embedding_cosine"
    detector_recomputed: bool = False
    llm_calls_required: int = 0
    embedding_model: str
    unique_text_count: int
    estimated_embedding_api_calls: int
    case_count: int
    candidate_count: int
    baseline_qualified_alert_count: int
    experimental_qualified_alert_count: int
    cases: tuple[AttributionExperimentCase, ...]
    caveats: tuple[str, ...] = Field(default_factory=tuple)


def _cluster_from_candidate(
    candidate: BenchmarkCandidateRecord,
    complaint_by_id: dict[str, Any],
) -> ComplaintCluster:
    member_complaints = [
        complaint_by_id[item_id]
        for item_id in candidate.member_ids
        if item_id in complaint_by_id
    ]
    if not member_complaints:
        raise RuntimeError(
            f"Candidate {candidate.signal_id} has no locally available member complaints"
        )
    try:
        embedding_method = EmbeddingMethod(candidate.embedding_method)
    except ValueError:
        embedding_method = EmbeddingMethod.NIM
    return ComplaintCluster(
        cluster_id=candidate.cluster_id or candidate.signal_id,
        label=candidate.issue,
        system=candidate.system,
        failure_mode=candidate.failure_mode,
        defect_family=candidate.defect_family,
        failure_mechanism=candidate.failure_mechanism,
        consequence_family=candidate.consequence_family,
        member_ids=candidate.member_ids,
        representative_complaint_ids=candidate.member_ids[:3],
        source_systems=candidate.source_systems,
        first_received_date=min(item.received_date for item in member_complaints),
        last_received_date=max(item.received_date for item in member_complaints),
        is_noise=False,
        is_meta=candidate.signal_scope == "meta",
        embedding_method=embedding_method,
    )


def _candidate_selection(case, top_target_count: int):
    occurrences: list[tuple[date, BenchmarkCandidateRecord]] = [
        (snapshot.cutoff_date, candidate)
        for snapshot in case.snapshots
        for candidate in snapshot.top_candidates
    ]
    alerts = [(cutoff, item) for cutoff, item in occurrences if item.alert]
    ranked = sorted(
        occurrences,
        key=lambda row: (
            -(row[1].posthoc_target_score or 0.0),
            row[0],
            row[1].signal_id,
        ),
    )[: max(1, top_target_count)]

    selected: dict[str, tuple[date, BenchmarkCandidateRecord, CandidateRole]] = {}
    for cutoff, item in ranked:
        selected[item.signal_id] = (cutoff, item, "top_target")
    for cutoff, item in alerts:
        # Alerts are the falsifiability guardrail. Keep them even if they already
        # appeared in the top-target selection, and preserve both roles.
        prior = selected.get(item.signal_id)
        role: CandidateRole = "alert_and_top_target" if prior is not None else "alert"
        selected[item.signal_id] = (cutoff, item, role)
    return sorted(selected.values(), key=lambda row: (row[0], row[1].signal_id))


async def run_attribution_experiment(
    *,
    settings: Settings,
    raw_result_path: Path,
    candidates_path: Path,
    target_match_threshold: float = 0.45,
    top_target_count: int = 10,
) -> AttributionExperimentResult:
    """Compare TF-IDF target attribution with a one-variable NIM embedding substitution.

    The function never invokes the detector, extractor, clusterer, trend engine, risk
    engine, or live visible-recall matcher. It uses frozen candidate/member IDs from the
    raw benchmark plus local complaint/signature caches. New network work is limited to
    the embedding calls for unique cluster/recall texts.
    """
    raw = BenchmarkRunResult.model_validate_json(raw_result_path.read_text(encoding="utf-8"))
    if not raw.freeze_verified or not raw.lock_verified:
        raise RuntimeError(
            "Attribution Experiment A requires a freeze-verified and lock-verified source benchmark"
        )

    manifest_cases = {item.case_id: item for item in load_candidates(candidates_path)}
    repository = FileRepository(settings.data_dir)
    embedding_client = NIMClient(
        api_key=settings.nvidia_api_key,
        base_url=settings.embedding_base_url or settings.nim_base_url,
        timeout_seconds=settings.request_timeout_seconds,
        max_retries=settings.max_retries,
        retry_base_delay_seconds=settings.retry_base_delay_seconds,
        retry_max_delay_seconds=settings.retry_max_delay_seconds,
    )
    if not embedding_client.configured:
        raise RuntimeError(
            "NIM embedding endpoint is not configured; set NVIDIA_API_KEY and/or "
            "RECALLZERO_EMBEDDING_BASE_URL"
        )
    embedder = NIMEmbedder(embedding_client, settings.embedding_model, batch_size=64)
    baseline_matcher = RecallMatcher()
    attributor = TargetAttributor(
        embedder=embedder,
        baseline_matcher=baseline_matcher,
        target_match_threshold=target_match_threshold,
    )

    prepared: list[dict[str, Any]] = []
    texts: dict[str, int] = {}
    text_values: list[str] = []

    def add_text(value: str) -> int:
        if value not in texts:
            texts[value] = len(text_values)
            text_values.append(value)
        return texts[value]

    for case in raw.cases:
        if (
            not case.benchmark_valid
            or case.expected_role != "positive"
            or case.status != "EARLY_ALERT_TARGET_UNMATCHED"
        ):
            continue
        manifest_case = manifest_cases.get(case.case_id)
        if manifest_case is None or not manifest_case.campaign_number:
            raise RuntimeError(f"Manifest metadata missing for case {case.case_id}")
        complaints = repository.load_complaints(case.vehicle) or []
        signatures = repository.load_signatures(case.vehicle)
        recalls = repository.load_recalls(case.vehicle) or []
        clean_campaign = manifest_case.campaign_number.upper().replace("-", "")
        target = next((item for item in recalls if item.campaign_number == clean_campaign), None)
        if target is None:
            raise RuntimeError(
                f"Target recall {clean_campaign} is not available in the local recall cache for {case.name}; "
                "the attribution experiment intentionally does not fetch new NHTSA data"
            )
        complaint_by_id = {item.odi_number: item for item in complaints}

        for cutoff, candidate, role in _candidate_selection(case, top_target_count):
            cluster = _cluster_from_candidate(candidate, complaint_by_id)
            member_signatures = [
                signatures[item_id]
                for item_id in candidate.member_ids
                if item_id in signatures
            ]
            member_complaints = [
                complaint_by_id[item_id]
                for item_id in candidate.member_ids
                if item_id in complaint_by_id
            ]
            if len(member_signatures) != len(set(candidate.member_ids)):
                missing = sorted(set(candidate.member_ids) - set(signatures))
                raise RuntimeError(
                    f"Candidate {candidate.signal_id} is missing {len(missing)} cached signature(s): "
                    + ", ".join(missing[:5])
                )
            baseline = baseline_matcher.score_target_details(
                cluster, member_signatures, target, member_complaints
            )
            persisted_baseline = candidate.posthoc_target_score
            if persisted_baseline is not None and abs(float(baseline["final"]) - persisted_baseline) > 0.002:
                raise RuntimeError(
                    f"Candidate {candidate.signal_id} baseline attribution recompute drifted from the frozen raw artifact: "
                    f"persisted={persisted_baseline:.4f}, recomputed={float(baseline['final']):.4f}. "
                    "Do not run the embedding experiment until cache/source provenance is reconciled."
                )
            cluster_text, recall_text = attributor.texts(cluster, member_signatures, target)
            prepared.append(
                {
                    "case": case,
                    "campaign": clean_campaign,
                    "cutoff": cutoff,
                    "candidate": candidate,
                    "role": role,
                    "baseline": baseline,
                    "cluster_text_idx": add_text(cluster_text),
                    "recall_text_idx": add_text(recall_text),
                }
            )

    if not prepared:
        raise RuntimeError(
            "No valid EARLY_ALERT_TARGET_UNMATCHED positive cases were found in the raw benchmark"
        )

    embedded = await embedder.embed(text_values)
    if embedded.method != EmbeddingMethod.NIM:
        raise RuntimeError(
            f"Experiment A requires NIM embeddings, got {embedded.method.value}"
        )
    matrix = np.asarray(embedded.matrix, dtype=np.float32)
    if matrix.shape[0] != len(text_values):
        raise RuntimeError(
            f"Embedding result count mismatch: expected {len(text_values)}, got {matrix.shape[0]}"
        )

    by_case: dict[str, list[AttributionExperimentCandidate]] = defaultdict(list)
    case_metadata: dict[str, tuple[str, str, str]] = {}
    for item in prepared:
        case = item["case"]
        candidate = item["candidate"]
        baseline = item["baseline"]
        similarity = TargetAttributor.cosine(
            matrix[item["cluster_text_idx"]], matrix[item["recall_text_idx"]]
        )
        experimental = TargetAttributor.substitute_embedding(baseline, similarity)
        baseline_final = float(baseline["final"])
        experiment_final = float(experimental["final_embedding_experiment"])
        record = AttributionExperimentCandidate(
            case_id=case.case_id,
            name=case.name,
            campaign_number=item["campaign"],
            cutoff_date=item["cutoff"],
            signal_id=candidate.signal_id,
            lineage_id=candidate.lineage_id,
            candidate_role=item["role"],
            alert=candidate.alert,
            issue=candidate.issue,
            signal_scope=candidate.signal_scope,
            risk_score=candidate.risk_score,
            evidence_count=candidate.evidence_count,
            component_compatible=float(baseline.get("component_compatible", 0.0)),
            failure_mechanism_score=float(baseline.get("failure_mechanism", 0.0)),
            consequence_family_score=float(baseline.get("consequence_family", 0.0)),
            component_score=float(baseline.get("component", 0.0)),
            subsystem_score=float(baseline.get("subsystem", 0.0)),
            consequence_score=float(baseline.get("consequence", 0.0)),
            baseline_semantic_lexical=float(baseline.get("semantic_lexical", 0.0)),
            embedding_similarity=round(similarity, 4),
            persisted_baseline_target_score=candidate.posthoc_target_score,
            baseline_recomputed_delta=(
                round(baseline_final - candidate.posthoc_target_score, 4)
                if candidate.posthoc_target_score is not None
                else None
            ),
            baseline_target_score=baseline_final,
            experimental_target_score=experiment_final,
            target_score_delta=round(experiment_final - baseline_final, 4),
            baseline_qualifies=baseline_final >= target_match_threshold,
            experimental_qualifies=experiment_final >= target_match_threshold,
            component_gate_closed=float(baseline.get("component_compatible", 0.0)) == 0.0,
        )
        by_case[case.case_id].append(record)
        case_metadata[case.case_id] = (case.name, item["campaign"], case.status)

    case_results: list[AttributionExperimentCase] = []
    for case_id, records in sorted(by_case.items()):
        name, campaign, status = case_metadata[case_id]
        alerts = [item for item in records if item.alert]
        target_rows = [
            item
            for item in records
            if item.candidate_role in {"top_target", "alert_and_top_target"}
        ]
        case_results.append(
            AttributionExperimentCase(
                case_id=case_id,
                name=name,
                campaign_number=campaign,
                detector_status=status,
                candidate_count=len(records),
                alert_candidate_count=len(alerts),
                top_target_candidate_count=len(target_rows),
                baseline_qualified_alert_count=sum(item.baseline_qualifies for item in alerts),
                experimental_qualified_alert_count=sum(item.experimental_qualifies for item in alerts),
                best_baseline_alert_score=max((item.baseline_target_score for item in alerts), default=None),
                best_experimental_alert_score=max((item.experimental_target_score for item in alerts), default=None),
                best_baseline_target_score=max((item.baseline_target_score for item in target_rows), default=None),
                best_experimental_target_score=max((item.experimental_target_score for item in target_rows), default=None),
                candidates=tuple(
                    sorted(records, key=lambda row: (row.cutoff_date, row.signal_id))
                ),
            )
        )

    all_records = [item for case in case_results for item in case.candidates]
    alert_records = [item for item in all_records if item.alert]
    return AttributionExperimentResult(
        source_benchmark_run_id=raw.benchmark_run_id,
        source_freeze_id=raw.freeze_id,
        source_freeze_verified=raw.freeze_verified,
        source_lock_verified=raw.lock_verified,
        target_match_threshold=target_match_threshold,
        embedding_model=settings.embedding_model,
        unique_text_count=len(text_values),
        estimated_embedding_api_calls=math.ceil(len(text_values) / 64),
        case_count=len(case_results),
        candidate_count=len(all_records),
        baseline_qualified_alert_count=sum(item.baseline_qualifies for item in alert_records),
        experimental_qualified_alert_count=sum(item.experimental_qualifies for item in alert_records),
        cases=tuple(case_results),
        caveats=(
            "Development diagnostic only: the source validation cohort has already been exposed and cannot serve as a fresh validation set.",
            "Experiment A changes only the 10% semantic term from TF-IDF cosine to raw NIM embedding cosine; structured weights, component gate, threshold, frozen risk scores, and alert decisions are unchanged.",
            "A higher score is not sufficient evidence of improvement; inspect discrimination so target-plausible candidates rise without unrelated alert candidates becoming target matches.",
            "No LLM extraction or detector replay is performed. Candidate clusters are reconstructed from frozen member IDs and local cached signatures/complaints.",
        ),
    )
