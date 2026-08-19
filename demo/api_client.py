from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class BacktestQuery:
    make: str
    model: str
    model_years: list[int]
    target_campaign_number: str
    official_recall_date: str
    replay_start_date: str | None = None
    refresh: bool = False
    use_nim: bool = True


class RecallZeroApi:
    def __init__(self, base_url: str, timeout: float = 180.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def readiness(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/demo/readiness")

    def evidence(self, odi_number: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/evidence/{odi_number.strip()}")

    def nvidia_trace(self, odi_number: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/demo/nvidia-trace",
            payload={"odi_number": odi_number.strip()},
            timeout=max(self.timeout, 240.0),
        )

    def backtest(self, query: BacktestQuery) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "make": query.make,
            "model": query.model,
            "model_years": query.model_years,
            "target_campaign_number": query.target_campaign_number,
            "official_recall_date": query.official_recall_date,
            "refresh": query.refresh,
            "use_nim": query.use_nim,
        }
        if query.replay_start_date:
            payload["replay_start_date"] = query.replay_start_date
        return self._request("POST", "/api/v1/backtests", payload=payload, timeout=max(self.timeout, 600.0))

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=timeout or self.timeout) as response:
                value = json.loads(response.read().decode("utf-8"))
                if not isinstance(value, dict):
                    raise ApiError("Backend returned a non-object JSON response")
                return value
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(detail)
                detail = str(parsed.get("detail", detail)) if isinstance(parsed, dict) else detail
            except json.JSONDecodeError:
                pass
            raise ApiError(f"HTTP {exc.code}: {detail}", status_code=exc.code) from exc
        except URLError as exc:
            raise ApiError(f"Could not reach RecallZero backend at {self.base_url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise ApiError(f"Backend request timed out after {timeout or self.timeout:g}s") from exc
