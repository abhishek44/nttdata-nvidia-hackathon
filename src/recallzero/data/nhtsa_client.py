from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from typing import Any

import httpx

from recallzero.data.normalizer import normalize_complaint, normalize_recall
from recallzero.models import Complaint, Recall, Vehicle

logger = logging.getLogger(__name__)


class NHTSAError(RuntimeError):
    """Base exception for NHTSA data-access failures."""


class NHTSAHTTPError(NHTSAError):
    """HTTP failure with enough context to support safe fallback decisions."""

    def __init__(
        self,
        *,
        status_code: int,
        url: str,
        response_text: str = "",
    ) -> None:
        self.status_code = int(status_code)
        self.url = url
        self.response_text = response_text.strip()
        detail = f"NHTSA returned HTTP {self.status_code} for {self.url}"
        if self.response_text:
            detail += f": {self.response_text[:300]}"
        super().__init__(detail)


class NHTSAClient:
    # Retrying a malformed/exact-match query only creates noise. Retry transport
    # failures, rate limiting, and server-side errors; fail fast for ordinary 4xx.
    _TRANSIENT_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}

    def __init__(
        self,
        base_url: str = "https://api.nhtsa.gov",
        timeout_seconds: float = 60,
        max_retries: int = 3,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._external_client = client
        self._model_catalog_cache: dict[tuple[str, int, str], tuple[str, ...]] = {}

    async def _request(self, path: str, params: dict[str, Any] | None) -> httpx.Response:
        if self._external_client is not None:
            return await self._external_client.get(path, params=params, timeout=self.timeout_seconds)
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_seconds) as client:
            return await client.get(path, params=params)

    async def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        last_error: Exception | None = None
        attempts = max(1, self.max_retries + 1)

        for attempt in range(attempts):
            try:
                response = await self._request(path, params)
                if response.status_code >= 400:
                    raise NHTSAHTTPError(
                        status_code=response.status_code,
                        url=str(response.url),
                        response_text=response.text,
                    )

                payload = response.json()
                if not isinstance(payload, dict):
                    raise NHTSAError("NHTSA response was not a JSON object")
                message = str(payload.get("message") or payload.get("Message") or "")
                if "error" in message.lower():
                    raise NHTSAError(message)
                return payload
            except NHTSAHTTPError as exc:
                last_error = exc
                if exc.status_code not in self._TRANSIENT_HTTP_STATUSES or attempt >= attempts - 1:
                    raise
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt >= attempts - 1:
                    break
            except (ValueError, NHTSAError) as exc:
                # Invalid JSON or an explicit API error is not expected to improve
                # through immediate retries.
                raise NHTSAError(str(exc)) from exc

            await asyncio.sleep(min(8.0, 0.5 * (2**attempt)))

        assert last_error is not None
        raise NHTSAError(str(last_error)) from last_error

    @staticmethod
    def _results(payload: dict[str, Any]) -> list[dict[str, Any]]:
        results = payload.get("results") or payload.get("Results") or []
        return [item for item in results if isinstance(item, dict)]

    @staticmethod
    def _model_identity(value: str) -> str:
        """Compare model names while ignoring catalog punctuation differences.

        NHTSA's complaint and recall catalogs can use different punctuation for the
        same vehicle (for example, ``MUSTANG MACH-E`` versus ``MUSTANG MACH E``).
        Removing non-alphanumeric characters gives an exact semantic identity without
        fuzzy-matching unrelated models.
        """

        return re.sub(r"[^A-Z0-9]+", "", value.upper())

    @staticmethod
    def _safe_model_variants(model: str) -> tuple[str, ...]:
        normalized_space = " ".join(re.sub(r"[^A-Z0-9]+", " ", model.upper()).split())
        values = [model, normalized_space]
        if "-" in model:
            values.append(" ".join(model.replace("-", " ").split()))
        dedup: list[str] = []
        for value in values:
            if value and value not in dedup:
                dedup.append(value)
        return tuple(dedup)

    async def fetch_available_models(self, *, make: str, year: int, issue_type: str) -> tuple[str, ...]:
        issue_type = issue_type.lower().strip()
        if issue_type not in {"c", "r"}:
            raise ValueError("issue_type must be 'c' for complaints or 'r' for recalls")
        key = (make.upper().strip(), int(year), issue_type)
        cached = self._model_catalog_cache.get(key)
        if cached is not None:
            return cached

        payload = await self._get_json(
            "/products/vehicle/models",
            params={"modelYear": year, "make": make, "issueType": issue_type},
        )
        models: list[str] = []
        for item in self._results(payload):
            value = item.get("model") or item.get("Model")
            if value is None:
                continue
            cleaned = " ".join(str(value).strip().upper().split())
            if cleaned and cleaned not in models:
                models.append(cleaned)
        result = tuple(models)
        self._model_catalog_cache[key] = result
        return result

    async def resolve_catalog_model(self, *, make: str, model: str, year: int, issue_type: str) -> str:
        """Resolve the exact model spelling expected by an NHTSA issue catalog.

        Values are matched only after punctuation/whitespace normalization. If the
        catalog is unavailable or no exact identity exists, the caller's model is
        retained and a conservative syntactic fallback is attempted by the endpoint
        method.
        """

        try:
            candidates = await self.fetch_available_models(make=make, year=year, issue_type=issue_type)
        except (NHTSAError, httpx.HTTPError) as exc:
            logger.warning(
                "Could not load NHTSA %s model catalog for %s %s: %s",
                "recall" if issue_type == "r" else "complaint",
                year,
                make,
                exc,
            )
            return model

        identity = self._model_identity(model)
        matches = [candidate for candidate in candidates if self._model_identity(candidate) == identity]
        if not matches:
            return model

        # Prefer the shortest exact-identity spelling, then lexical order for
        # deterministic behavior when the API catalog contains duplicates.
        resolved = sorted(matches, key=lambda value: (len(value), value))[0]
        if resolved != model.upper().strip():
            logger.info(
                "Resolved NHTSA %s model '%s' to catalog value '%s' for %s %s",
                "recall" if issue_type == "r" else "complaint",
                model,
                resolved,
                year,
                make,
            )
        return resolved

    async def fetch_complaints_for_year(self, vehicle: Vehicle, year: int) -> tuple[list[Complaint], dict[str, Any]]:
        params = {"make": vehicle.make, "model": vehicle.model, "modelYear": year}
        payload = await self._get_json("/complaints/complaintsByVehicle", params=params)
        complaints: list[Complaint] = []
        for item in self._results(payload):
            try:
                complaints.append(
                    normalize_complaint(
                        item,
                        Vehicle(make=vehicle.make, model=vehicle.model, model_years=(year,)),
                    )
                )
            except (ValueError, TypeError) as exc:
                logger.warning("Skipping invalid complaint: %s", exc)
        return complaints, payload

    async def fetch_complaints(self, vehicle: Vehicle) -> tuple[list[Complaint], dict[str, Any]]:
        responses = await asyncio.gather(*(self.fetch_complaints_for_year(vehicle, year) for year in vehicle.model_years))
        dedup: dict[str, Complaint] = {}
        raw_by_year: dict[str, Any] = {}
        for year, (items, raw) in zip(vehicle.model_years, responses, strict=True):
            raw_by_year[str(year)] = raw
            for complaint in items:
                dedup[complaint.odi_number] = complaint
        return sorted(dedup.values(), key=lambda item: (item.received_date, item.odi_number)), raw_by_year

    async def _fetch_recalls_payload(self, *, make: str, model: str, year: int) -> tuple[dict[str, Any], str]:
        resolved = await self.resolve_catalog_model(
            make=make,
            model=model,
            year=year,
            issue_type="r",
        )
        variants = (resolved, *self._safe_model_variants(model))
        attempted: list[str] = []
        last_error: NHTSAHTTPError | None = None

        for candidate in variants:
            candidate = " ".join(candidate.strip().upper().split())
            if not candidate or candidate in attempted:
                continue
            attempted.append(candidate)
            try:
                payload = await self._get_json(
                    "/recalls/recallsByVehicle",
                    params={"make": make, "model": candidate, "modelYear": year},
                )
                payload = dict(payload)
                payload.setdefault(
                    "recallzeroQuery",
                    {
                        "requestedModel": model,
                        "resolvedModel": candidate,
                        "modelYear": year,
                        "make": make,
                    },
                )
                return payload, candidate
            except NHTSAHTTPError as exc:
                last_error = exc
                # A 400 commonly means that the recall catalog expects a different
                # exact model spelling. Try only safe punctuation variants; propagate
                # every other status immediately.
                if exc.status_code != 400:
                    raise

        if last_error is not None:
            tried = ", ".join(attempted)
            raise NHTSAError(
                f"NHTSA recall lookup rejected all exact model variants for {year} {make} {model}. "
                f"Tried: {tried}. Last error: {last_error}"
            ) from last_error
        raise NHTSAError(f"No usable NHTSA model variant was generated for {year} {make} {model}")

    async def fetch_recalls_for_year(self, vehicle: Vehicle, year: int) -> tuple[list[Recall], dict[str, Any]]:
        payload, query_model = await self._fetch_recalls_payload(
            make=vehicle.make,
            model=vehicle.model,
            year=year,
        )
        recalls: list[Recall] = []
        requested_vehicle = Vehicle(make=vehicle.make, model=query_model, model_years=(year,))
        for item in self._results(payload):
            try:
                recalls.append(normalize_recall(item, requested_vehicle))
            except (ValueError, TypeError) as exc:
                logger.warning("Skipping invalid recall: %s", exc)
        return recalls, payload

    async def fetch_recalls(self, vehicle: Vehicle) -> tuple[list[Recall], dict[str, Any]]:
        responses = await asyncio.gather(*(self.fetch_recalls_for_year(vehicle, year) for year in vehicle.model_years))
        dedup: dict[str, Recall] = {}
        raw_by_year: dict[str, Any] = {}
        for year, (items, raw) in zip(vehicle.model_years, responses, strict=True):
            raw_by_year[str(year)] = raw
            for recall in items:
                existing = dedup.get(recall.campaign_number)
                if existing is None or (recall.summary and not existing.summary):
                    dedup[recall.campaign_number] = recall
        return sorted(
            dedup.values(),
            key=lambda item: (item.report_received_date or date.min, item.campaign_number),
        ), raw_by_year

    async def fetch_campaign(self, campaign_number: str) -> Recall | None:
        """Fetch one campaign through NHTSA's campaignNumber endpoint.

        The endpoint may return one row per affected make/model/year. The campaign-level
        description is shared, so the first valid row is sufficient for RecallZero's
        post-hoc backtest matching.
        """
        clean = campaign_number.upper().replace("-", "").strip()
        if not clean:
            raise ValueError("campaign_number must not be empty")
        try:
            payload = await self._get_json(
                "/recalls/campaignNumber",
                params={"campaignNumber": clean},
            )
        except (httpx.HTTPError, NHTSAError):
            return None
        for item in self._results(payload):
            try:
                return normalize_recall(item, None)
            except (ValueError, TypeError):
                continue
        return None
