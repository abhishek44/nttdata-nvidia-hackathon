from fastapi.testclient import TestClient

from recallzero.api.app import create_app
from recallzero.config import Settings


def test_health_and_dashboard(tmp_path) -> None:
    client = TestClient(create_app(Settings(data_dir=tmp_path, use_nim=False)))
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "RecallZero Safety Radar" in dashboard.text


def test_offline_demo_endpoint(tmp_path) -> None:
    client = TestClient(create_app(Settings(data_dir=tmp_path, use_nim=False)))
    response = client.get("/api/v1/demo")
    assert response.status_code == 200
    payload = response.json()
    assert payload["analysis"]["complaint_count"] > 0
    assert payload["analysis"]["signals"]
    assert payload["backtest"]["status"] in {"EARLY_SIGNAL_DETECTED", "EARLY_ALERT_TARGET_UNMATCHED", "NO_EARLY_SIGNAL"}
    assert all(payload["backtest"]["anti_leakage_checks"].values())
