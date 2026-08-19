from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from api_client import ApiError, BacktestQuery, RecallZeroApi


HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
DEFAULT_API_URL = os.getenv("RECALLZERO_API_URL", "http://127.0.0.1:8080")
DEFAULT_ODI = os.getenv("RECALLZERO_DEMO_ODI", "11466150")
REFERENCE_BACKTEST = ASSETS / "mach_e_reference_backtest.json"
VALIDATION_SUMMARY = ASSETS / "detector_v1_validation_summary.json"

st.set_page_config(
    page_title="RecallZero Live Demo",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed",
)


@st.cache_data(show_spinner=False)
def load_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@st.cache_data(ttl=5, show_spinner=False)
def fetch_readiness(base_url: str) -> dict[str, Any]:
    return RecallZeroApi(base_url, timeout=10).readiness()


@st.cache_data(ttl=5, show_spinner=False)
def fetch_action_readiness(base_url: str) -> dict[str, Any]:
    return RecallZeroApi(base_url, timeout=10).action_readiness()


def api() -> RecallZeroApi:
    return RecallZeroApi(DEFAULT_API_URL)


def inject_style() -> None:
    st.markdown(
        """
        <style>
        .stApp { background: #07101a; color: #f8fafc; }
        [data-testid="stHeader"], #MainMenu, footer { visibility: hidden; height: 0; }
        .block-container { padding-top: 1.15rem; padding-bottom: 2rem; max-width: 1500px; }
        h1,h2,h3 { letter-spacing: -0.02em; }
        .rz-hero { border:1px solid rgba(148,163,184,.18); border-radius:16px; padding:18px 22px;
                   background:linear-gradient(130deg,#0b1726,#0a1220); margin-bottom:14px; }
        .rz-kicker { color:#a3e635; font-size:.78rem; font-weight:800; letter-spacing:.15em; }
        .rz-title { font-size:2rem; font-weight:850; margin:.15rem 0 .25rem; }
        .rz-sub { color:#94a3b8; margin:0; }
        .rz-chip { display:inline-block; padding:7px 10px; margin:3px 5px 3px 0; border-radius:999px;
                   border:1px solid rgba(148,163,184,.22); background:#0d1928; font-size:.78rem; }
        .rz-green { color:#bef264; border-color:rgba(163,230,53,.35); }
        .rz-blue { color:#7dd3fc; border-color:rgba(56,189,248,.35); }
        .rz-slate { color:#cbd5e1; }
        .rz-lock { border:1px dashed rgba(245,158,11,.48); background:rgba(245,158,11,.06);
                   border-radius:12px; padding:14px; color:#fde68a; }
        .rz-reveal { border:1px solid rgba(163,230,53,.45); background:rgba(163,230,53,.07);
                     border-radius:12px; padding:14px; }
        .rz-note { color:#94a3b8; font-size:.9rem; }
        .rz-ai { border-left:4px solid #76b900; padding-left:14px; }
        .rz-action { border:1px solid rgba(56,189,248,.28); background:rgba(56,189,248,.05);
                     border-radius:14px; padding:16px; margin:.35rem 0 .8rem; }
        .rz-action-high { border-color:rgba(245,158,11,.38); background:rgba(245,158,11,.06); }
        .rz-workflow { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin:.5rem 0 1rem; }
        .rz-step { border:1px solid rgba(148,163,184,.2); border-radius:10px; padding:7px 10px; color:#cbd5e1; background:#0c1726; }
        .rz-arrow { color:#64748b; font-weight:800; }
        div[data-testid="stMetric"] { background:#0c1726; border:1px solid rgba(148,163,184,.16);
                                      padding:12px; border-radius:12px; }
        div[data-testid="stTabs"] button { font-weight:700; }
        .stProgress > div > div > div > div { background-color:#76b900; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def service_strip(readiness: dict[str, Any] | None, error: str | None) -> None:
    if error:
        st.markdown(
            f'<span class="rz-chip rz-slate">Backend unavailable: {error}</span>',
            unsafe_allow_html=True,
        )
        return
    assert readiness is not None
    services = readiness.get("services", {})
    extraction = services.get("extraction", {})
    embedding = services.get("embedding", {})
    risk = services.get("risk_engine", {})
    checks = readiness.get("checks", {})
    nim_ok = bool(checks.get("nim_extraction_configured"))
    emb_ok = bool(checks.get("nvidia_embeddings_configured"))
    cached = bool(checks.get("demo_complaint_cached"))
    st.markdown(
        "".join(
            [
                f'<span class="rz-chip {"rz-green" if nim_ok else "rz-slate"}">● NVIDIA NIM · {extraction.get("model", "not configured")}</span>',
                f'<span class="rz-chip {"rz-green" if emb_ok else "rz-slate"}">● NVIDIA Embeddings · {embedding.get("model", "not configured")}</span>',
                f'<span class="rz-chip rz-blue">◆ Deterministic Risk · threshold {risk.get("alert_threshold", 75)}</span>',
                f'<span class="rz-chip {"rz-green" if cached else "rz-slate"}">Evidence cache · {"ready" if cached else "missing demo ODI"}</span>',
            ]
        ),
        unsafe_allow_html=True,
    )


def risk_factor_rows(signal: dict[str, Any]) -> list[dict[str, Any]]:
    if "risk" in signal:
        return list(signal.get("risk", {}).get("factors", []))
    factors = signal.get("risk_factors", {})
    return [
        {
            "name": name,
            "score": value.get("score", 0),
            "weight": value.get("weight", 0),
            "contribution": value.get("contribution", 0),
            "explanation": value.get("explanation", ""),
        }
        for name, value in factors.items()
    ]


def signal_summary(signal: dict[str, Any]) -> tuple[str, float, int, bool]:
    if "risk" in signal:
        cluster = signal.get("cluster", {})
        return (
            str(cluster.get("label", "Signal")),
            float(signal.get("risk", {}).get("final_score", 0)),
            len(cluster.get("member_ids", [])),
            bool(signal.get("risk", {}).get("alert", False)),
        )
    return (
        str(signal.get("issue", "Candidate")),
        float(signal.get("risk_score", 0)),
        int(signal.get("evidence_count", 0)),
        bool(signal.get("alert", False)),
    )


def selected_signal(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    alerts = snapshot.get("alerts") or []
    if alerts:
        return alerts[0]
    candidates = snapshot.get("top_candidates") or []
    return candidates[0] if candidates else None


def default_snapshot_index(backtest: dict[str, Any]) -> int:
    target_date = backtest.get("first_qualified_alert_date") or backtest.get("first_matching_alert_date")
    snapshots = backtest.get("snapshots") or []
    if target_date:
        for index, snapshot in enumerate(snapshots):
            if snapshot.get("cutoff_date") == target_date:
                return index
    return max(0, len(snapshots) - 1)


def render_live_investigation(readiness: dict[str, Any] | None) -> None:
    st.subheader("1 · Live NVIDIA Investigation")
    st.caption("One real NHTSA complaint → fresh NVIDIA NIM extraction → fresh NVIDIA embedding. No signature-cache write and no detector score is changed.")

    preset = st.selectbox(
        "Demo complaint",
        [
            ("11466150", "Vehicle shutdown / Stop Safely Now"),
            ("11464572", "High-voltage battery junction box diagnosis"),
            ("11459465", "Power loss / tow required"),
        ],
        format_func=lambda item: f"ODI {item[0]} — {item[1]}",
    )
    odi = st.text_input("ODI number", value=preset[0], help="Must already exist in RecallZero's normalized complaint cache.")

    complaint: dict[str, Any] | None = None
    try:
        complaint = api().evidence(odi)
    except ApiError as exc:
        st.warning(str(exc))

    if complaint:
        c1, c2, c3 = st.columns([1, 1, 2])
        c1.metric("ODI", complaint.get("odi_number"))
        c2.metric("Received", complaint.get("received_date"))
        c3.metric("NHTSA components", ", ".join(complaint.get("components") or []) or "—")
        st.markdown("**Original complaint narrative**")
        st.info(complaint.get("narrative", ""))

    can_run = bool(readiness and readiness.get("ready_for_live_trace")) and bool(complaint)
    if st.button("Run fresh NVIDIA inference", type="primary", disabled=not can_run, use_container_width=True):
        try:
            with st.status("Running NVIDIA services…", expanded=True) as status:
                st.write("1. Sending complaint to NVIDIA NIM for guided structured extraction")
                trace = api().nvidia_trace(odi)
                st.write("2. Embedding the canonical failure signature with NVIDIA embeddings")
                st.write("3. Returning trace metadata without modifying detector caches")
                status.update(label="Fresh NVIDIA trace complete", state="complete")
            st.session_state["nvidia_trace"] = trace
        except ApiError as exc:
            st.error(str(exc))

    trace = st.session_state.get("nvidia_trace")
    if not trace:
        if not can_run:
            st.caption("Live trace is disabled until the backend reports NIM, embeddings, and the demo complaint cache as ready.")
        return

    extraction = trace["extraction"]
    embedding = trace["embedding"]
    signature = extraction["signature"]
    a, b, c, d = st.columns(4)
    a.metric("Fresh NIM", f'{extraction["latency_ms"] / 1000:.2f}s')
    b.metric("Fresh embedding", f'{embedding["latency_ms"] / 1000:.2f}s')
    c.metric("Vector dimension", embedding["dimension"])
    d.metric("Cache write", "NO")

    st.markdown('<div class="rz-ai"><b>NVIDIA-generated failure signature</b></div>', unsafe_allow_html=True)
    cols = st.columns(3)
    fields = [
        ("System", signature.get("system")),
        ("Subsystem", signature.get("subsystem")),
        ("Failure mode", signature.get("failure_mode")),
        ("Operating state", signature.get("operating_state")),
        ("Consequence", signature.get("consequence")),
        ("Confidence", signature.get("confidence")),
    ]
    for idx, (label, value) in enumerate(fields):
        cols[idx % 3].metric(label, value if value not in (None, "") else "—")
    st.write("Severity indicators:", ", ".join(signature.get("severity_indicators") or []) or "none")

    with st.expander("Show NVIDIA service trace"):
        st.json(
            {
                "extraction_model": extraction["model"],
                "extraction_endpoint_kind": extraction["endpoint_kind"],
                "embedding_model": embedding["model"],
                "embedding_endpoint_kind": embedding["endpoint_kind"],
                "embedding_dimension": embedding["dimension"],
                "embedding_preview": embedding["preview"],
                "fresh_inference": trace["fresh_inference"],
                "cache_write": trace["cache_write"],
            }
        )

    with st.expander("Where AI is used — and where it is not"):
        left, right = st.columns(2)
        with left:
            st.markdown("**NVIDIA AI**")
            for item in trace["decision_boundary"]["ai_used_for"]:
                st.write(f"✓ {item}")
        with right:
            st.markdown("**Deterministic RecallZero**")
            for item in trace["decision_boundary"]["ai_not_used_for"]:
                st.write(f"✓ {item}")


def render_backtest(backtest: dict[str, Any], source_label: str) -> None:
    snapshots = backtest.get("snapshots") or []
    if not snapshots:
        st.warning("No backtest snapshots available.")
        return

    st.markdown(f"**Replay source:** {source_label}")
    st.caption("Target recall text is excluded from detector scoring. Post-hoc matching happens only after each historical snapshot is frozen.")

    frame = pd.DataFrame(
        {
            "cutoff": [item.get("cutoff_date") for item in snapshots],
            "Max risk": [float(item.get("max_risk_score") or 0) for item in snapshots],
            "Alert threshold": [75.0] * len(snapshots),
        }
    ).set_index("cutoff")
    st.line_chart(frame, height=250)

    idx = st.slider(
        "Historical cutoff",
        min_value=0,
        max_value=len(snapshots) - 1,
        value=default_snapshot_index(backtest),
        format="%d",
        key=f"snapshot_{source_label}",
        help="Move through frozen historical snapshots. The recall outcome remains hidden until Reveal is clicked.",
    )
    snapshot = snapshots[idx]
    st.caption(f'Snapshot {idx + 1}/{len(snapshots)} · cutoff {snapshot.get("cutoff_date")}')

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Visible complaints", snapshot.get("complaint_count_visible", 0))
    s2.metric("Signals", snapshot.get("signal_count", 0))
    s3.metric("Max risk", snapshot.get("max_risk_score", 0))
    s4.metric("Alerts", len(snapshot.get("alerts") or []))

    signal = selected_signal(snapshot)
    if signal:
        st.session_state["selected_demo_signal"] = signal
        st.session_state["selected_demo_signal_source"] = source_label
        st.session_state["selected_demo_backtest_meta"] = {
            "target_campaign_number": backtest.get("target_campaign_number"),
            "official_recall_date": backtest.get("official_recall_date"),
            "first_qualified_alert_date": backtest.get("first_qualified_alert_date"),
            "first_matching_alert_date": backtest.get("first_matching_alert_date"),
            "lead_time_days": backtest.get("lead_time_days"),
        }
        issue, score, evidence_count, alerted = signal_summary(signal)
        st.markdown(f"### {'🚨' if alerted else '◌'} {issue}")
        m1, m2, m3 = st.columns(3)
        m1.metric("Risk", f"{score:.2f} / 100")
        m2.metric("Evidence", evidence_count)
        m3.metric("Detector gate", "ALERT" if alerted else "below 75")

        st.markdown("**Why this score?**")
        factors = risk_factor_rows(signal)
        if factors:
            for factor in factors:
                label = str(factor.get("name", "factor")).replace("_", " ").title()
                value = float(factor.get("score") or 0)
                weight = float(factor.get("weight") or 0)
                st.write(f"{label} — {value:.1f} · weight {weight:.0%}")
                st.progress(min(1.0, max(0.0, value / 100.0)))
                if factor.get("explanation"):
                    st.caption(str(factor["explanation"]))

        evidence = signal.get("evidence") or []
        member_ids = list(signal.get("cluster", {}).get("member_ids") or signal.get("member_ids") or [])
        st.markdown("**Traceable evidence**")
        if evidence:
            rows = [
                {
                    "ODI": item.get("complaint_id"),
                    "Received": item.get("received_date"),
                    "Excerpt": item.get("narrative_excerpt"),
                }
                for item in evidence
            ]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, height=210)
            evidence_ids = [str(item.get("complaint_id")) for item in evidence if item.get("complaint_id")]
        else:
            evidence_ids = member_ids
            st.caption("This persisted candidate stores member ODI IDs; select one below to fetch the full cached complaint.")

        if evidence_ids:
            chosen_odi = st.selectbox("Open original complaint", evidence_ids, key=f"evidence_{source_label}_{idx}")
            try:
                original = api().evidence(chosen_odi)
                st.info(original.get("narrative", ""))
            except ApiError as exc:
                st.caption(f"Full complaint not available from the active GB10 cache: {exc}")

    st.divider()
    if not st.session_state.get("reveal_recall", False):
        st.markdown(
            '<div class="rz-lock"><b>🔒 Historical recall outcome hidden from detector</b><br>'
            'The detector only sees complaints and recalls already public at each cutoff.</div>',
            unsafe_allow_html=True,
        )
        if st.button("Reveal historical outcome", type="primary", use_container_width=True):
            st.session_state["reveal_recall"] = True
            st.rerun()
    else:
        lead = backtest.get("lead_time_days")
        official = backtest.get("official_recall_date")
        first = backtest.get("first_qualified_alert_date") or backtest.get("first_matching_alert_date")
        st.markdown(
            f'<div class="rz-reveal"><b>HISTORICAL OUTCOME REVEALED</b><br>'
            f'Campaign {backtest.get("target_campaign_number")} · official recall {official}<br>'
            f'RecallZero qualified alert: {first or "none"} · lead time: {lead if lead is not None else "n/a"} days</div>',
            unsafe_allow_html=True,
        )
        if st.button("Hide recall again"):
            st.session_state["reveal_recall"] = False
            st.rerun()

    checks = backtest.get("anti_leakage_checks") or {}
    if checks:
        with st.expander("Anti-leakage checks"):
            for name, passed in checks.items():
                st.write(f'{"✓" if passed else "✗"} {name.replace("_", " ")}')


def render_time_machine() -> None:
    st.subheader("2 · Time Machine")
    st.caption("Use the audited reference replay for the guaranteed presentation story. A live quick replay is available as an optional backend demonstration.")

    left, right = st.columns([1.15, 1])
    with left:
        if st.button("Load audited Mach-E reference replay", use_container_width=True):
            st.session_state["active_backtest"] = load_json(str(REFERENCE_BACKTEST))
            st.session_state["active_backtest_source"] = "Audited reference replay · Detector v1 historical artifact"
            st.session_state["reveal_recall"] = False
    with right:
        if st.button("Run live quick replay · Apr 7 → Jun 9, 2022", type="primary", use_container_width=True):
            query = BacktestQuery(
                make="FORD",
                model="MUSTANG MACH-E",
                model_years=[2021, 2022],
                target_campaign_number="22V412000",
                official_recall_date="2022-06-10",
                replay_start_date="2022-04-07",
                refresh=False,
                use_nim=True,
            )
            try:
                with st.status("Running live historical replay…", expanded=True) as status:
                    st.write("Using cached NHTSA records and cached failure signatures")
                    st.write("Recomputing clustering, trend, severity, evidence, recall-gap and risk at each cutoff")
                    st.write("NVIDIA embeddings may be called during clustering")
                    result = api().backtest(query)
                    status.update(label="Live Time Machine complete", state="complete")
                st.session_state["active_backtest"] = result
                st.session_state["active_backtest_source"] = "Live quick replay · current GB10 backend"
                st.session_state["reveal_recall"] = False
            except ApiError as exc:
                st.error(f"Live replay failed: {exc}")
                st.info("The audited reference replay remains available and is the recommended presentation fallback.")

    if "active_backtest" not in st.session_state:
        st.session_state["active_backtest"] = load_json(str(REFERENCE_BACKTEST))
        st.session_state["active_backtest_source"] = "Audited reference replay · Detector v1 historical artifact"
    render_backtest(st.session_state["active_backtest"], st.session_state["active_backtest_source"])


def reference_alert_signal() -> dict[str, Any] | None:
    backtest = load_json(str(REFERENCE_BACKTEST))
    target_date = backtest.get("first_qualified_alert_date") or backtest.get("first_matching_alert_date")
    snapshots = backtest.get("snapshots") or []
    for snapshot in snapshots:
        if target_date and snapshot.get("cutoff_date") != target_date:
            continue
        alerts = snapshot.get("alerts") or []
        if alerts:
            return alerts[0]
    for snapshot in snapshots:
        alerts = snapshot.get("alerts") or []
        if alerts:
            return alerts[0]
    return None


def render_action_center() -> None:
    st.subheader("3 · Engineering Action Center")
    st.caption(
        "Turn a detected signal into a human investigation workflow: deterministic quality brief, traceable evidence package, optional webhook escalation, and optional NVIDIA NeMo Agent Toolkit orchestration."
    )
    st.markdown(
        '<div class="rz-workflow"><span class="rz-step">Detect</span><span class="rz-arrow">→</span>'
        '<span class="rz-step">Inspect evidence</span><span class="rz-arrow">→</span>'
        '<span class="rz-step">Create engineering brief</span><span class="rz-arrow">→</span>'
        '<span class="rz-step">Escalate / investigate</span></div>',
        unsafe_allow_html=True,
    )

    with st.expander("Optional · investigate another vehicle live"):
        st.caption(
            "This uses the real /api/v1/analyze path. Keep refresh off for a presentation so cached NHTSA records and cached NIM signatures are reused where available."
        )
        q1, q2, q3 = st.columns([1, 1.3, 2])
        live_year = q1.number_input("Model year", min_value=1990, max_value=date.today().year + 1, value=2021, step=1)
        live_make = q2.text_input("Make", value="FORD")
        live_model = q3.text_input("Model", value="MUSTANG MACH-E")
        max_complaints = st.number_input(
            "Maximum complaints for optional live investigation",
            min_value=10,
            max_value=1000,
            value=100,
            step=10,
            help="A presentation guardrail. Cached signatures are reused; embeddings/clustering may still execute.",
        )
        if st.button("Analyze selected vehicle", use_container_width=True):
            try:
                with st.status("Running RecallZero vehicle investigation…", expanded=True) as status:
                    st.write("Fetching/reusing NHTSA complaint and recall data")
                    st.write("Reusing cached NVIDIA signatures where present")
                    st.write("Running deterministic clustering, trend, evidence, severity and risk analytics")
                    result = api().analyze(
                        make=live_make,
                        model=live_model,
                        model_years=[int(live_year)],
                        refresh=False,
                        use_nim=True,
                        max_complaints=int(max_complaints),
                    )
                    status.update(label="Vehicle investigation complete", state="complete")
                st.session_state["live_analysis"] = result
                signals = result.get("signals") or []
                if signals:
                    st.session_state["action_signal_override"] = signals[0]
                    st.session_state["action_signal_override_source"] = f'Live analysis · run {result.get("run_id", "current")}'
                    st.session_state.pop("investigation_packet", None)
            except ApiError as exc:
                st.error(f"Live vehicle analysis failed: {exc}")
        if st.session_state.get("live_analysis"):
            result = st.session_state["live_analysis"]
            signals = result.get("signals") or []
            x1, x2, x3 = st.columns(3)
            x1.metric("Complaints", result.get("complaint_count", 0))
            x2.metric("Signals", len(signals))
            x3.metric("Alerts", sum(1 for item in signals if (item.get("risk") or {}).get("alert")))
            if signals:
                labels = [signal_summary(item)[0] for item in signals[:10]]
                chosen = st.selectbox("Choose a signal for the Action Center", range(len(labels)), format_func=lambda i: labels[i])
                st.session_state["action_signal_override"] = signals[chosen]
                st.session_state["action_signal_override_source"] = f'Live analysis · run {result.get("run_id", "current")}'

    if st.session_state.get("action_signal_override") and st.button("Use Time Machine signal instead", use_container_width=True):
        st.session_state.pop("action_signal_override", None)
        st.session_state.pop("action_signal_override_source", None)
        st.session_state.pop("investigation_packet", None)
        st.rerun()

    signal = st.session_state.get("action_signal_override") or st.session_state.get("selected_demo_signal") or reference_alert_signal()
    source_label = st.session_state.get("action_signal_override_source") or st.session_state.get(
        "selected_demo_signal_source",
        "Audited reference replay · Detector v1 historical artifact",
    )
    if not signal:
        st.warning("No persisted signal is available for the Action Center.")
        return

    issue, score, evidence_count, alerted = signal_summary(signal)
    trend = signal.get("trend") or {}
    recall_match = signal.get("recall_match") or {}
    recommendation = "Escalate for engineering review" if alerted else ("Priority monitoring" if score >= 60 else "Continue monitoring")
    st.markdown(
        f'<div class="rz-action {"rz-action-high" if alerted else ""}"><b>{recommendation}</b><br>'
        f'{issue}<br><span class="rz-note">Risk {score:.1f}/100 · {evidence_count} supporting complaints · '
        f'{float(trend.get("trend_ratio") or 0):.2f}x recent/baseline ratio</span></div>',
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Risk", f"{score:.1f} / 100")
    c2.metric("Evidence", evidence_count)
    c3.metric("Trend", f'{float(trend.get("trend_ratio") or 0):.2f}x')
    c4.metric("Visible recall match", "YES" if recall_match.get("matched") else "NO")

    st.markdown("#### Quality Engineering Brief")
    st.write(
        "The brief is generated from the already-computed detector signal. It does not call an LLM, invent a new score, or change the detector."
    )
    if st.button("Generate engineering investigation packet", type="primary", use_container_width=True):
        try:
            historical_outcome = None
            if st.session_state.get("reveal_recall"):
                historical_outcome = st.session_state.get("selected_demo_backtest_meta")
            with st.spinner("Building deterministic investigation packet…"):
                packet = api().investigation_brief(
                    signal,
                    source_label=source_label,
                    historical_outcome=historical_outcome,
                )
            st.session_state["investigation_packet"] = packet
        except ApiError as exc:
            st.error(f"Could not generate the engineering packet: {exc}")

    packet = st.session_state.get("investigation_packet")
    if packet:
        action = packet.get("recommended_action") or {}
        st.success(f'{action.get("title", "Investigation packet ready")} — {action.get("summary", "")}')
        top = (packet.get("risk_factors") or [])[:3]
        if top:
            st.markdown("**Top deterministic reasons**")
            for row in top:
                st.write(
                    f'• {str(row.get("name", "factor")).replace("_", " ").title()}: '
                    f'{float(row.get("score") or 0):.1f}/100 · contribution {float(row.get("contribution") or 0):.1f}'
                )
        with st.expander("Open the full engineering brief", expanded=True):
            st.markdown(packet.get("markdown", ""))
        d1, d2 = st.columns(2)
        d1.download_button(
            "Download brief (.md)",
            data=packet.get("markdown", ""),
            file_name="recallzero_engineering_investigation.md",
            mime="text/markdown",
            use_container_width=True,
        )
        d2.download_button(
            "Download evidence packet (.json)",
            data=json.dumps({k: v for k, v in packet.items() if k != "markdown"}, indent=2),
            file_name="recallzero_engineering_investigation.json",
            mime="application/json",
            use_container_width=True,
        )

    st.divider()
    st.markdown("#### Proactive escalation")
    try:
        action_ready = fetch_action_readiness(DEFAULT_API_URL)
    except ApiError as exc:
        action_ready = {"alert": {"configured": False}, "agent": {"available": False, "execution_enabled": False}}
        st.caption(f"Action readiness unavailable: {exc}")

    alert_state = action_ready.get("alert") or {}
    left, right = st.columns([1, 2])
    with left:
        st.metric("Webhook", "READY" if alert_state.get("configured") else "OPTIONAL / OFF")
    with right:
        if alert_state.get("configured"):
            st.caption(f'Provider: {alert_state.get("provider")} · destination credentials are never rendered in the UI.')
        else:
            st.caption(
                "Optional: configure RECALLZERO_DEMO_ALERT_WEBHOOK_URL to send the same evidence-grounded investigation summary to a Slack/Teams/generic webhook."
            )
    if st.button(
        "Send investigation alert",
        disabled=not bool(alert_state.get("configured")),
        use_container_width=True,
    ):
        try:
            result = api().send_alert(signal, source_label=source_label)
            st.success(f'Alert sent through {result.get("provider")} · HTTP {result.get("http_status")}')
        except ApiError as exc:
            st.error(f"Alert delivery failed: {exc}")

    st.divider()
    st.markdown("#### NVIDIA Investigator Agent · optional live orchestration")
    agent = action_ready.get("agent") or {}
    a1, a2, a3 = st.columns(3)
    a1.metric("NeMo Agent Toolkit", "AVAILABLE" if agent.get("available") else "NOT INSTALLED")
    a2.metric("Agent execution", "ENABLED" if agent.get("execution_enabled") else "SAFE-OFF")
    a3.metric("Workflow", agent.get("workflow") or "tool_calling_agent")
    st.caption(
        "The agent orchestrates existing RecallZero tools. It does not replace deterministic trend, evidence, risk, or anti-leakage calculations."
    )
    with st.expander("Agent tool map"):
        st.write("NVIDIA model:", agent.get("agent_model", "—"))
        for tool in agent.get("tools") or []:
            st.write(f"✓ {tool}")

    default_prompt = (
        "Investigate the 2021 and 2022 Ford Mustang Mach-E. Summarize the highest-priority emerging signal, "
        "cite source ODI evidence, and explain what RecallZero recommends an engineer review."
    )
    prompt = st.text_area("Investigator prompt", value=default_prompt, height=100)
    can_agent = bool(agent.get("available") and agent.get("execution_enabled"))
    if st.button("Run NVIDIA Investigator Agent", disabled=not can_agent, use_container_width=True):
        try:
            with st.status("NVIDIA Investigator Agent is orchestrating RecallZero tools…", expanded=True) as status:
                result = api().agent_investigate(prompt)
                status.update(label="Investigator workflow complete", state="complete")
            st.session_state["agent_result"] = result
        except ApiError as exc:
            st.error(f"Agent workflow failed: {exc}")
    if not can_agent:
        st.info(
            "Keep this optional path disabled for the main demo unless NAT has been rehearsed on GB10. Enable it with RECALLZERO_DEMO_ENABLE_AGENT=true after installing the [aiq] extra."
        )
    if st.session_state.get("agent_result"):
        result = st.session_state["agent_result"]
        st.markdown("**Agent response**")
        st.code(result.get("output", ""), language="text")


def render_validation() -> None:
    st.subheader("4 · Validation Lab")
    summary = load_json(str(VALIDATION_SUMMARY))
    agg = summary["aggregate"]
    st.caption("Frozen preregistered Detector v1 validation. This screen is intentionally a read-only research artifact.")

    st.error("Detector v1 preregistered acceptance: FAIL")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Target-qualified sensitivity", f'{agg["qualified_positive_count"]}/{agg["valid_positive_count"]}', f'{agg["positive_sensitivity"]*100:.0f}%')
    # Any-alert positive rate is not an aggregate field in the frozen summary; derive from case statuses only for display.
    positive_cases = [case for case in summary["cases"] if case.get("expected_role") == "positive" and case.get("benchmark_valid")]
    any_alert = sum(1 for case in positive_cases if int(case.get("alert_snapshot_count") or 0) > 0)
    c2.metric("Positive vehicles with any alert", f"{any_alert}/{len(positive_cases)}", "not recall sensitivity")
    c3.metric("Controls with unconfirmed alert", f'{agg["control_cases_with_unconfirmed_alerts"]}/{agg["valid_control_count"]}', f'{agg["control_cases_with_unconfirmed_alerts_rate"]*100:.1f}%')
    c4.metric("Invalid cases", agg["invalid_case_count"])

    a, b = st.columns(2)
    a.success(f'Anti-leakage checks: {"PASS" if agg["all_anti_leakage_checks_pass"] else "FAIL"}')
    b.success(f'Freeze / benchmark lock: {"PASS" if summary["freeze_verified"] and summary["lock_verified"] else "FAIL"}')

    rows = []
    for case in summary["cases"]:
        rows.append(
            {
                "Role": case.get("expected_role"),
                "Case": case.get("name"),
                "Status": case.get("status"),
                "Max risk": case.get("max_risk_score"),
                "Lead days": case.get("lead_time_days"),
                "Valid": case.get("benchmark_valid"),
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, height=420)

    st.markdown("#### What we learned")
    st.write("• A pre-recall alert anywhere on a vehicle is not the same as detecting the target recall defect.")
    st.write("• Embedding-based attribution improved semantic discrimination but added zero qualified alerts in the exposed cohort.")
    st.write("• Long-horizon recurrence separated in the wrong direction: controls were more recurrent than positives, so that feature was rejected.")
    st.write("• RecallZero Detector v1 remains a stable research baseline, not a production-validated detector.")


def main() -> None:
    inject_style()
    st.markdown(
        """
        <div class="rz-hero">
          <div class="rz-kicker">RECALLZERO · LIVE DEFECT INTELLIGENCE DEMO</div>
          <div class="rz-title">Can safety-relevant complaint patterns be surfaced before a recall is public?</div>
          <p class="rz-sub">NVIDIA AI understands the complaint language. RecallZero's deterministic engine decides the risk. Evidence becomes an engineering action packet, while historical outcomes stay locked until evaluation.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    readiness = None
    readiness_error = None
    try:
        readiness = fetch_readiness(DEFAULT_API_URL)
    except ApiError as exc:
        readiness_error = str(exc)
    service_strip(readiness, readiness_error)

    with st.expander("Demo runtime details"):
        st.code(f"RecallZero API: {DEFAULT_API_URL}\nReference replay: {REFERENCE_BACKTEST.name}\nValidation artifact: {VALIDATION_SUMMARY.name}")
        if readiness:
            st.json(readiness)

    tab1, tab2, tab3, tab4 = st.tabs(["1 · LIVE NVIDIA", "2 · TIME MACHINE", "3 · ACTION CENTER", "4 · VALIDATION"])
    with tab1:
        render_live_investigation(readiness)
    with tab2:
        render_time_machine()
    with tab3:
        render_action_center()
    with tab4:
        render_validation()


if __name__ == "__main__":
    main()
