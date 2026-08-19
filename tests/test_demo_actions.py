from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from recallzero.api.app import create_app
from recallzero.config import Settings
from recallzero.demo_actions import agent_readiness, alert_readiness, build_investigation_packet


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "demo" / "assets" / "mach_e_reference_backtest.json"


def _reference_signal() -> dict[str, object]:
    backtest = json.loads(REFERENCE.read_text(encoding="utf-8"))
    target_date = backtest["first_qualified_alert_date"]
    snapshot = next(item for item in backtest["snapshots"] if item["cutoff_date"] == target_date)
    return snapshot["alerts"][0]


def test_investigation_packet_is_derived_without_recomputing_detector() -> None:
    packet = build_investigation_packet(
        _reference_signal(),
        source_label="unit-test",
        historical_outcome={"campaign": "22V412000"},
    )

    assert packet["detector_values_recomputed"] is False
    assert packet["ai_generated_recommendation"] is False
    assert packet["priority"]["risk_score"] == 80.23
    assert packet["priority"]["alert"] is True
    assert packet["evidence"]["supporting_complaints"] == 12
    assert packet["recommended_action"]["code"] == "ENGINEERING_REVIEW"
    assert "22V412000" not in packet["markdown"]  # historical outcome is metadata, not mixed into live investigation text
    assert "RecallZero ranks evidence" in packet["markdown"]


def test_action_readiness_is_safe_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv("RECALLZERO_DEMO_ALERT_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("RECALLZERO_DEMO_ENABLE_AGENT", raising=False)

    alert = alert_readiness()
    agent = agent_readiness()

    assert alert["configured"] is False
    assert agent["execution_enabled"] is False
    assert agent["workflow"] == "tool_calling_agent"
    assert "recallzero_engineering_brief" in agent["tools"]


def test_demo_investigation_brief_route(tmp_path) -> None:
    client = TestClient(create_app(Settings(data_dir=tmp_path, use_nim=False)))
    response = client.post(
        "/api/v1/demo/investigation-brief",
        json={"signal": _reference_signal(), "source_label": "test-route"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_label"] == "test-route"
    assert payload["priority"]["risk_score"] == 80.23
    assert payload["recommended_action"]["title"] == "Escalate for engineering review"


def test_demo_alert_route_uses_server_generated_packet(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_send(packet):  # noqa: ANN001
        captured.update(packet)
        return {"sent": True, "provider": "test", "http_status": 200}

    monkeypatch.setattr("recallzero.api.routes.send_investigation_alert", fake_send)
    client = TestClient(create_app(Settings(data_dir=tmp_path, use_nim=False)))
    response = client.post(
        "/api/v1/demo/send-alert",
        json={"signal": _reference_signal(), "source_label": "test-alert"},
    )

    assert response.status_code == 200
    assert response.json()["sent"] is True
    assert captured["source_label"] == "test-alert"
    assert captured["detector_values_recomputed"] is False


def test_agent_endpoint_fails_closed_when_disabled(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("RECALLZERO_DEMO_ENABLE_AGENT", raising=False)
    client = TestClient(create_app(Settings(data_dir=tmp_path, use_nim=False)))
    response = client.post(
        "/api/v1/demo/agent-investigate",
        json={"prompt": "Investigate the 2021 Ford Mustang Mach-E."},
    )

    assert response.status_code == 503
    assert "disabled" in response.json()["detail"].lower()
