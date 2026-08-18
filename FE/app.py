from __future__ import annotations

import os
from html import escape
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from api_client import ApiError, RecallZeroApi, VehicleQuery


DEFAULT_API_URL = os.getenv("RECALLZERO_API_URL", "http://localhost:8000")
DEFAULT_VEHICLE = VehicleQuery(year=2021, make="Ford", model="Mustang Mach-E")

st.set_page_config(page_title="RecallZero", page_icon="RZ", layout="wide", initial_sidebar_state="collapsed")


@st.cache_data(ttl=120, show_spinner=False)
def load_dashboard(base_url: str, year: int, make: str, model: str) -> dict[str, Any]:
    vehicle = VehicleQuery(year=year, make=make, model=model)
    return RecallZeroApi(base_url).dashboard(vehicle)


def icon(name: str) -> str:
    icons = {
        "radar": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 12l5-5"/><path d="M8 12a4 4 0 0 1 4-4"/><path d="M12 16a4 4 0 0 0 4-4"/></svg>',
        "search": '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>',
        "clock": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
        "recall": '<svg viewBox="0 0 24 24"><path d="M7 7h10v10H7z"/><path d="M4 12h3"/><path d="M17 12h3"/><path d="M12 4v3"/><path d="M12 17v3"/></svg>',
        "data": '<svg viewBox="0 0 24 24"><ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6"/><path d="M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/></svg>',
        "stack": '<svg viewBox="0 0 24 24"><path d="M12 3l8 4-8 4-8-4 8-4z"/><path d="M4 12l8 4 8-4"/><path d="M4 17l8 4 8-4"/></svg>',
        "warning": '<svg viewBox="0 0 24 24"><path d="M12 3l10 18H2L12 3z"/><path d="M12 9v5"/><path d="M12 18h.01"/></svg>',
        "trend": '<svg viewBox="0 0 24 24"><path d="M3 17l6-6 4 4 7-8"/><path d="M14 7h6v6"/></svg>',
    }
    return icons.get(name, icons["radar"])


def compact(value: Any) -> str:
    if isinstance(value, (int, float)) and value >= 1000:
        return f"{value:,}"
    return str(value)


def nav_html(payload: dict[str, Any]) -> str:
    nav = payload["ui"]["nav"]
    return "".join(
        f'<div class="{"active" if item.get("active") else ""}"><i class="ico">{icon(str(item.get("icon")))}</i>{escape(str(item.get("label")))}</div>'
        for item in nav
    )


def kpi_html(payload: dict[str, Any]) -> str:
    return "".join(
        f"""
        <div class="kpi {escape(str(item.get("tone")))}">
          <i class="kpi-ico">{icon(str(item.get("icon")))}</i>
          <b>{escape(str(item.get("label")))}</b>
          <strong>{escape(compact(item.get("value")))}</strong>
          <span>{escape(str(item.get("hint")))}</span>
        </div>
        """
        for item in payload["kpis"]
    )


def signal_html(payload: dict[str, Any]) -> str:
    html = []
    for index, signal in enumerate(payload["signals"]):
        metrics = signal.get("metrics") or []
        metric_cells = "".join(
            f'<div class="mini"><strong>{escape(str(metric.get("value")))}</strong><span>{escape(str(metric.get("label")))}</span></div>'
            for metric in metrics[:3]
        )
        html.append(
            f"""
            <div class="signal-row {'selected' if signal.get('selected', index == 0) else ''} tone-{escape(str(signal.get('tone') or 'slate'))}">
              <div class="signal-icon">{icon('warning') if index == 0 else icon('radar')}</div>
              <div class="signal-copy"><em>{escape(str(signal.get("status")))}</em><strong>{escape(str(signal.get("title")))}</strong><span>{escape(str(signal.get("subtitle")))}</span></div>
              <div class="risk-score">{escape(str(signal.get("risk_score")))}<span>/100</span></div>
              {metric_cells}
            </div>
            <p class="candidate-note">{escape(str(signal.get("note") or ""))}</p>
            """
        )
    return "".join(html)


def trend_svg(payload: dict[str, Any]) -> str:
    trend = payload["trend"]
    timeline = trend["timeline"]
    if not timeline:
        return '<svg class="trend" viewBox="0 0 360 150"><text x="180" y="78" class="chart-label" text-anchor="middle">No trend timeline available</text></svg>'
    scores = [float(point.get("risk_score") or 0) for point in timeline]
    high = max(max(scores), float(trend.get("threshold") or 70), 100)
    low = min(min(scores), 0)
    plot_left, plot_top, plot_width, plot_height = 16, 8, 330, 112
    points = []
    for index, score in enumerate(scores):
        x = plot_left + plot_width * index / max(1, len(scores) - 1)
        y = plot_top + plot_height - ((score - low) / max(1, high - low)) * plot_height
        points.append(f"{x:.1f},{y:.1f}")
    threshold = float(trend.get("threshold") or 70)
    threshold_y = plot_top + plot_height - ((threshold - low) / max(1, high - low)) * plot_height
    end_x, end_y = points[-1].split(",")
    start_x, start_y = points[0].split(",")
    alert_marker = ""
    if trend.get("alert_date"):
        for index, point in enumerate(timeline):
            if point.get("date") == trend["alert_date"]:
                alert_x = plot_left + plot_width * index / max(1, len(timeline) - 1)
                alert_score = float(point.get("risk_score") or 0)
                alert_y = plot_top + plot_height - ((alert_score - low) / max(1, high - low)) * plot_height
                alert_marker = f'<circle cx="{alert_x:.1f}" cy="{alert_y:.1f}" r="5.5" class="alert-dot"/><text x="{alert_x + 8:.1f}" y="{alert_y - 8:.1f}" class="chart-label">First alert</text>'
                break
    return f'''
    <svg class="trend" viewBox="0 0 360 150">
      <line x1="{plot_left}" y1="{plot_top}" x2="{plot_left}" y2="{plot_top + plot_height}" class="axis"></line>
      <line x1="{plot_left}" y1="{plot_top + plot_height}" x2="{plot_left + plot_width}" y2="{plot_top + plot_height}" class="axis"></line>
      <line x1="{plot_left}" y1="{threshold_y:.1f}" x2="{plot_left + plot_width}" y2="{threshold_y:.1f}" class="threshold"></line>
      <text x="{plot_left + 4}" y="{threshold_y - 4:.1f}" class="chart-label">Alert threshold {threshold:g}</text>
      <text x="4" y="{plot_top + 10}" class="chart-label">{escape(str(trend.get("y_label") or "Risk"))}</text>
      <polyline class="trend-line" points="{" ".join(points)}"/>
      <circle cx="{start_x}" cy="{start_y}" r="4.5" class="point-dot"/>
      <circle cx="{end_x}" cy="{end_y}" r="5.5" class="end-dot"/>
      {alert_marker}
      <text x="{plot_left}" y="136" class="chart-label">{escape(str(trend.get("x_start")))}</text>
      <text x="{plot_left + plot_width}" y="136" class="chart-label" text-anchor="end">{escape(str(trend.get("x_end")))}</text>
      <text x="357" y="{float(end_y) + 4:.1f}" class="end-label" text-anchor="end">{scores[-1]:g}</text>
    </svg>
    '''


def complaints_html(payload: dict[str, Any]) -> str:
    complaints = payload["complaints"]
    if not complaints:
        return f'<tr><td colspan="6">{escape(str(payload["complaints_empty"]))}</td></tr>'
    return "".join(
        f"""
        <tr>
          <td>{escape(str(row.get("index")))}</td>
          <td>{escape(str(row.get("odi")))}</td>
          <td>{escape(str(row.get("date")))}</td>
          <td>{escape(", ".join(map(str, row.get("components") or [])))}</td>
          <td>{escape(str(row.get("summary")))[:150]}</td>
          <td>{''.join(f'<b>{escape(str(tag))}</b>' for tag in (row.get("evidence") or []))}</td>
        </tr>
        """
        for row in complaints
    )


def recalls_html(payload: dict[str, Any]) -> str:
    recalls = payload["recalls"]
    if not recalls:
        return f'<tr><td colspan="4">{escape(str(payload["recalls_empty"]))}</td></tr>'
    return "".join(
        f"""
        <tr>
          <td>{escape(str(row.get("campaign")))}</td>
          <td>{escape(str(row.get("date")))}</td>
          <td>{escape(str(row.get("component")))}</td>
          <td>{escape(str(row.get("summary")))[:180]}</td>
        </tr>
        """
        for row in recalls
    )


def build_html(payload: dict[str, Any]) -> str:
    ui = payload["ui"]
    status = payload["status"]
    signature = payload["signature"]
    trend = payload["trend"]
    details = payload["details"]
    time_machine = payload["time_machine"]
    filters = "".join(f'<span class="{"on" if index == 0 else ""}">{escape(str(label))}</span>' for index, label in enumerate(ui["filters"]))

    return f"""
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8" />
      <style>
        :root {{ --bg:#07101a; --panel:#0c1624; --panel2:#101b2a; --line:rgba(148,163,184,.16); --text:#f8fafc; --sub:#8ea0b8; --muted:#64748b; --green:#84cc16; --red:#ef4444; --orange:#f59e0b; --blue:#38bdf8; }}
        *{{box-sizing:border-box}} html,body{{width:100%;height:100%;margin:0;background:var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;font-size:12px;overflow:hidden}}
        @keyframes pulse{{0%,100%{{box-shadow:0 0 0 0 rgba(132,204,22,.58);opacity:1}}50%{{box-shadow:0 0 0 8px rgba(132,204,22,0);opacity:.72}}}}
        @keyframes floatIcon{{0%,100%{{transform:translateY(0) scale(1)}}50%{{transform:translateY(-4px) scale(1.04)}}}}
        @keyframes sweep{{0%{{transform:translateX(-130%) skewX(-18deg);opacity:0}}18%{{opacity:.45}}42%,100%{{transform:translateX(360%) skewX(-18deg);opacity:0}}}}
        @keyframes ripple{{0%,100%{{box-shadow:0 0 0 0 var(--tone-border)}}50%{{box-shadow:0 0 0 7px rgba(148,163,184,0)}}}}
        @keyframes drawLine{{from{{stroke-dashoffset:700}}to{{stroke-dashoffset:0}}}}
        @keyframes glowEdge{{0%,100%{{border-color:var(--tone-border)}}50%{{border-color:var(--tone)}}}}
        @keyframes countPop{{from{{transform:translateY(4px);opacity:.35}}to{{transform:translateY(0);opacity:1}}}}
        .app{{height:100vh;display:grid;grid-template-columns:184px minmax(0,1fr);background:var(--bg)}}
        aside{{background:#08111d;border-right:1px solid var(--line);padding:18px 10px;position:relative}}
        .brand{{font-size:19px;font-weight:850;margin-bottom:18px}} .brand span{{color:var(--green)}}
        nav{{display:grid;gap:5px}} nav div{{height:34px;display:flex;align-items:center;gap:9px;padding:0 10px;border-radius:6px;color:#b8c7da;transition:.18s ease}} nav div.active{{background:rgba(132,204,22,.11);color:#d9f99d;border-left:2px solid var(--green)}} nav div:hover{{background:rgba(148,163,184,.08);color:#f8fafc}} .ico{{width:24px;height:24px;display:grid;place-items:center;border:1px solid var(--line);border-radius:6px;background:rgba(148,163,184,.07);animation:floatIcon 2.8s ease-in-out infinite}} .ico svg,.kpi-ico svg,.signal-icon svg{{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}}
        .system{{position:absolute;left:12px;right:12px;bottom:14px;border-top:1px solid var(--line);padding-top:12px}} .system h3{{font-size:12px;margin:0 0 8px}} .dot{{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--green);margin-right:7px;animation:pulse 1.9s ease-in-out infinite}} .system p{{margin:5px 0;color:var(--sub);font-size:11px}}
        main{{padding:16px 18px;height:100vh;overflow:hidden;display:flex;flex-direction:column}} .topbar{{height:34px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line);margin-bottom:12px;flex:0 0 auto}} .live{{font-size:11px;font-weight:780;color:#d9f99d}} .filters span{{display:inline-block;border:1px solid var(--line);padding:5px 9px;border-radius:5px;color:var(--sub);margin-left:5px}} .filters span.on{{color:#d9f99d;border-color:rgba(132,204,22,.45)}}
        h1{{margin:0 0 3px;font-size:24px;line-height:1.1}} .subtitle{{color:var(--sub);margin-bottom:12px}}
        .kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:10px;flex:0 0 auto}} .kpi{{height:86px;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 42px 13px 16px;position:relative;overflow:hidden}} .kpi:before{{content:"";position:absolute;left:-1px;top:12px;bottom:12px;width:3px;border-radius:8px}} .kpi:after{{content:"";position:absolute;top:0;bottom:0;width:42px;background:linear-gradient(90deg,transparent,rgba(255,255,255,.08),transparent);animation:sweep 5.2s ease-in-out infinite}} .kpi:nth-child(2):after{{animation-delay:.7s}} .kpi:nth-child(3):after{{animation-delay:1.4s}} .kpi:nth-child(4):after{{animation-delay:2.1s}} .kpi-ico{{position:absolute;right:12px;top:12px;width:25px;height:25px;border-radius:7px;border:1px solid var(--line);background:rgba(148,163,184,.06);display:grid;place-items:center;animation:floatIcon 2.2s ease-in-out infinite}} .kpi.red:before{{background:var(--red)}} .kpi.orange:before{{background:var(--orange)}} .kpi.blue:before{{background:var(--blue)}} .kpi.green:before{{background:var(--green)}} .kpi.red .kpi-ico{{color:var(--red)}} .kpi.orange .kpi-ico{{color:var(--orange)}} .kpi.blue .kpi-ico{{color:var(--blue)}} .kpi.green .kpi-ico{{color:var(--green)}} .kpi b{{display:block;color:#dbe7f5;margin-bottom:7px;line-height:1.15}} .kpi strong{{font-size:26px;line-height:.95;display:block;animation:countPop .45s ease both}} .kpi span{{display:block;color:var(--muted);margin-top:8px;line-height:1.2}}
        .layout{{display:grid;grid-template-columns:minmax(0,2.45fr) minmax(360px,.9fr);gap:14px;flex:1 1 auto;min-height:0;margin-bottom:10px}} .left,.right{{min-width:0;min-height:0;display:grid;gap:10px}} .left{{grid-template-rows:auto minmax(172px,.72fr) minmax(170px,1fr)}} .right{{grid-template-rows:minmax(190px,.92fr) minmax(146px,.55fr) minmax(170px,.8fr)}} .card{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:13px;margin:0;min-height:0;overflow:hidden}} .section-title{{font-weight:800;font-size:12px;letter-spacing:.04em;color:#dbe7f5;margin-bottom:10px}}
        .signal-row{{--tone:#94a3b8;--tone-bg:rgba(148,163,184,.045);--tone-border:rgba(148,163,184,.22);height:62px;display:grid;grid-template-columns:56px minmax(250px,1.6fr) 78px repeat(3,96px);align-items:center;gap:12px;padding:9px 12px;margin-bottom:8px;background:var(--panel);border:1px solid var(--line);border-radius:8px;transition:.18s ease}} .signal-row:hover{{transform:translateY(-1px);background:var(--panel2)}} .signal-row.selected{{background:var(--tone-bg);border-color:var(--tone-border);box-shadow:inset 3px 0 0 var(--tone);animation:glowEdge 2.8s ease-in-out infinite}} .signal-icon{{width:36px;height:36px;border-radius:50%;border:1px solid var(--tone-border);display:grid;place-items:center;color:var(--tone);background:var(--tone-bg);animation:floatIcon 2.1s ease-in-out infinite,ripple 2.6s ease-in-out infinite}} .signal-copy em{{display:block;color:var(--tone);font-style:normal;font-size:9px;font-weight:800}} .signal-copy strong{{display:block;font-size:13px}} .signal-copy span,.mini span{{display:block;color:var(--muted);font-size:10.5px}} .risk-score{{color:var(--tone);font-size:23px;font-weight:850;animation:countPop .55s ease both}} .risk-score span{{color:#cbd5e1;font-size:10px}} .mini strong{{font-size:12px}} .candidate-note{{color:#b7c6d8;margin:3px 0 0 4px}} .tone-red{{--tone:#f87171;--tone-bg:rgba(239,68,68,.055);--tone-border:rgba(239,68,68,.35)}} .tone-orange{{--tone:#fbbf24;--tone-bg:rgba(245,158,11,.06);--tone-border:rgba(245,158,11,.34)}} .tone-blue{{--tone:#38bdf8;--tone-bg:rgba(56,189,248,.055);--tone-border:rgba(56,189,248,.30)}} .tone-purple{{--tone:#a78bfa;--tone-bg:rgba(167,139,250,.055);--tone-border:rgba(167,139,250,.30)}} .tone-green{{--tone:#84cc16;--tone-bg:rgba(132,204,22,.055);--tone-border:rgba(132,204,22,.30)}} .tone-slate{{--tone:#94a3b8;--tone-bg:rgba(148,163,184,.045);--tone-border:rgba(148,163,184,.22)}}
        .overview{{display:grid;grid-template-columns:1.15fr 1fr;gap:10px;min-height:0;height:100%}} .overview .card{{height:100%}} .trend-card{{display:grid;grid-template-rows:auto minmax(108px,1fr) auto 30px;gap:3px}} .sig-grid{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}} .sig-tile{{background:#08111d;border:1px solid var(--line);border-radius:7px;padding:9px;min-height:54px;transition:.18s ease}} .sig-tile:hover{{border-color:rgba(132,204,22,.32);transform:translateY(-1px)}} .sig-tile span{{display:block;color:var(--muted);font-size:10px;text-transform:uppercase}} .sig-tile strong{{display:block;margin-top:4px}} .trend{{width:100%;height:100%;min-height:108px;margin:0}} .axis{{stroke:rgba(148,163,184,.22);stroke-width:1.15}} .threshold{{stroke:rgba(245,158,11,.62);stroke-width:1.35;stroke-dasharray:4 4}} .chart-label{{fill:#8ea0b8;font-size:10px}} .end-label{{fill:#bef264;font-size:12.5px;font-weight:800}} .point-dot{{fill:#0c1624;stroke:#84cc16;stroke-width:2.25}} .end-dot{{fill:#84cc16;stroke:#d9f99d;stroke-width:2.25}} .alert-dot{{fill:#f59e0b;stroke:#fde68a;stroke-width:2.25}} .trend-line{{fill:none;stroke:var(--green);stroke-width:3.8;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:700;animation:drawLine 1.5s ease both}} .trend-summary{{color:#94a3b8;font-size:10.5px;line-height:1.2;margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}} .velocity{{display:flex;justify-content:space-between;align-items:flex-start;color:var(--sub);min-height:30px;overflow:hidden}} .velocity strong{{font-size:24px;line-height:1;color:var(--red);animation:countPop .55s ease both}} .velocity b{{color:#dbe7f5;line-height:1.1}} .velocity span{{line-height:1.15}}
        .table-scroll{{height:calc(100% - 26px);overflow:auto;padding-right:4px}} .table-scroll::-webkit-scrollbar{{width:6px;height:6px}} .table-scroll::-webkit-scrollbar-thumb{{background:rgba(148,163,184,.28);border-radius:999px}} .table-scroll::-webkit-scrollbar-track{{background:transparent}}
        table{{width:100%;border-collapse:collapse;table-layout:fixed}} th{{position:sticky;top:0;background:var(--panel);z-index:2;color:var(--muted);text-align:left;font-size:10px;text-transform:uppercase;padding:0 7px 7px;border-bottom:1px solid var(--line)}} td{{color:#cbd5e1;padding:8px 7px;border-bottom:1px solid rgba(148,163,184,.08);font-size:11px;vertical-align:middle;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}} tr:hover td{{background:rgba(148,163,184,.035)}} td b{{display:inline-block;border:1px solid var(--line);border-radius:5px;padding:2px 5px;margin:1px;color:#dbe7f5;background:#08111d;font-weight:520}}
        .details h2{{font-size:18px;margin:7px 0 6px}} .badge{{display:inline-block;border-radius:5px;padding:4px 7px;font-size:10px;font-weight:800;color:#f87171;background:rgba(239,68,68,.10);border:1px solid rgba(239,68,68,.35)}} .detail-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin:10px 0}} .detail-grid div{{background:#08111d;border:1px solid var(--line);border-radius:7px;padding:8px}} .detail-grid span{{display:block;color:var(--muted);font-size:10px}} .detail-grid strong{{display:block;margin-top:3px}} .time strong.big{{display:block;color:var(--green);font-size:32px;text-align:center;margin-top:10px}} .time span.center{{display:block;text-align:center;color:#bef264}} .status{{font-size:11px;color:var(--sub)}} .quiet{{display:inline-block;color:#fbbf24;background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.22);border-radius:6px;padding:5px 7px;margin-top:7px}}
      </style>
    </head>
    <body>
      <div class="app">
        <aside>
          <div class="brand">{escape(str(ui["brand"]["prefix"]))}<span>{escape(str(ui["brand"]["accent"]))}</span></div>
          <nav>{nav_html(payload)}</nav>
          <div class="system">
            <h3>System Status</h3>
            <p><i class="dot"></i>FastAPI: {escape(str(status["fastapi"]))}</p>
            <p><i class="dot"></i>NHTSA Data: {escape(str(status["nhtsa_data"]))}</p>
            <p><i class="dot"></i>Signal Engine: {escape(str(status["signal_engine"]))}</p>
          </div>
        </aside>
        <main>
          <div class="topbar"><div class="live"><i class="dot"></i>{escape(str(ui["live_label"]))}</div><div class="filters">{filters}</div></div>
          <h1>{escape(str(ui["title"]))}</h1>
          <div class="kpis">{kpi_html(payload)}</div>
          <div class="subtitle">Top components: {escape(str(payload["top_components"]))}</div>
          <div class="layout">
            <section class="left">
              <div class="card"><div class="section-title">TOP EMERGING SIGNALS</div>{signal_html(payload)}<span class="quiet">{escape(str(status["issue_label"]))}</span></div>
              <div class="overview">
                <div class="card">
                  <div class="section-title">AI FAILURE SIGNATURE</div>
                  <div class="sig-grid">
                    <div class="sig-tile"><span>System</span><strong>{escape(str(signature["system"]))}</strong></div>
                    <div class="sig-tile"><span>Subsystem</span><strong>{escape(str(signature["subsystem"]))}</strong></div>
                    <div class="sig-tile"><span>Failure mode</span><strong>{escape(str(signature["failure_mode"]))}</strong></div>
                    <div class="sig-tile"><span>Operating state</span><strong>{escape(str(signature["operating_state"]))}</strong></div>
                    <div class="sig-tile"><span>Consequence</span><strong>{escape(str(signature["consequence"]))}</strong></div>
                    <div class="sig-tile"><span>Confidence</span><strong>{escape(str(signature["confidence"]))}</strong></div>
                  </div>
                </div>
                <div class="card trend-card">
                  <div class="section-title">{escape(str(trend["title"]))} <span class="status">{escape(str(trend["label"]))}</span></div>
                  {trend_svg(payload)}
                  <p class="trend-summary">{escape(str(trend.get("summary") or ""))}</p>
                  <div class="velocity"><div><b>{escape(str(trend["velocity_label"]))}</b><br><span>{escape(str(trend["velocity_hint"]))}</span></div><strong>{escape(str(trend["score"]))}</strong></div>
                </div>
              </div>
              <div class="card">
                <div class="section-title">LATEST SUPPORTING COMPLAINTS</div>
                <div class="table-scroll"><table><thead><tr><th>#</th><th>ODI</th><th>Date</th><th>Components</th><th>Summary</th><th>Evidence</th></tr></thead><tbody>{complaints_html(payload)}</tbody></table></div>
              </div>
            </section>
            <section class="right">
              <div class="card details">
                <div class="section-title">SIGNAL DETAILS</div>
                <span class="badge">{escape(str(details["badge"]))}</span>
                <h2>{escape(str(details["title"]))[:88]}</h2>
                <p class="status">{escape(str(details["subtitle"]))}</p>
                <div class="detail-grid">
                  <div><span>Risk Score</span><strong>{escape(str(details["risk_score"]))}/100</strong></div>
                  <div><span>First Alert</span><strong>{escape(str(details["first_alert"]))}</strong></div>
                  <div><span>Matching Recall</span><strong>{escape(str(details["matching_recall"]))}</strong></div>
                </div>
              </div>
              <div class="card time">
                <div class="section-title">TIME MACHINE</div>
                <div class="detail-grid">
                  <div><span>Known recall</span><strong>{escape(str(time_machine["known_recall"]))}</strong></div>
                  <div><span>Alert threshold</span><strong>{escape(str(time_machine["alert_threshold"]))}</strong></div>
                  <div><span>Replay steps</span><strong>{escape(str(time_machine["replay_steps"]))}</strong></div>
                </div>
                <strong class="big">{escape(str(time_machine["lead_time_days"]))}</strong>
                <span class="center">{escape(str(time_machine["lead_label"]))}</span>
              </div>
              <div class="card">
                <div class="section-title">RECALL RECORDS</div>
                <div class="table-scroll"><table><thead><tr><th>Campaign</th><th>Date</th><th>Component</th><th>Summary</th></tr></thead><tbody>{recalls_html(payload)}</tbody></table></div>
              </div>
            </section>
          </div>
        </main>
      </div>
    </body>
    </html>
    """


def main() -> None:
    st.markdown(
        """
        <style>
        html, body, .stApp, [data-testid="stAppViewContainer"], .main {
            height:100vh !important;
            overflow:hidden !important;
            background:#07101a !important;
        }
        .block-container {
            padding:0 !important;
            max-width:none !important;
            height:100vh !important;
            overflow:hidden !important;
        }
        [data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stSidebar"], #MainMenu, footer { display:none !important; }
        iframe { display:block; border:0; height:calc(100vh - 1px) !important; min-height:0 !important; overflow:hidden !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    try:
        payload = load_dashboard(DEFAULT_API_URL, DEFAULT_VEHICLE.year, DEFAULT_VEHICLE.make, DEFAULT_VEHICLE.model)
    except ApiError as exc:
        st.error(f"Backend dashboard endpoint is unavailable: {exc}")
        return
    components.html(build_html(payload), height=720, scrolling=False)


if __name__ == "__main__":
    main()
