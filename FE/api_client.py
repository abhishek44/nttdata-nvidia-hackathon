from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class ApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class VehicleQuery:
    year: int
    make: str
    model: str


class RecallZeroApi:
    def __init__(self, base_url: str, timeout: float = 45.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def dashboard(self, vehicle: VehicleQuery) -> dict[str, Any]:
        make = quote(vehicle.make.strip(), safe="")
        model = quote(vehicle.model.strip(), safe="")
        return self._get(f"/dashboard?year={vehicle.year}&make={make}&model={model}")

    def _get(self, path: str) -> dict[str, Any]:
        request = Request(f"{self.base_url}{path}", headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ApiError(f"Backend returned HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise ApiError(f"Could not reach backend at {self.base_url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise ApiError(f"Backend request timed out after {self.timeout:g}s") from exc
