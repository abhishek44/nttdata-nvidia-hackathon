from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlparse

import httpx



class NIMError(RuntimeError):
    pass


class NIMClient:
    """Small OpenAI-compatible client for hosted NVIDIA APIs or a local NIM."""

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str,
        timeout_seconds: float = 90,
        max_retries: int = 3,
        client: httpx.AsyncClient | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._external_client = client

    @property
    def configured(self) -> bool:
        if self.api_key:
            return True
        parsed = urlparse(self.base_url)
        # Hosted NVIDIA endpoints require credentials. Local/LAN OpenAI-compatible
        # NIM endpoints may intentionally run without an API key.
        hosted_nvidia_hosts = {"integrate.api.nvidia.com", "api.nvidia.com"}
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and parsed.hostname not in hosted_nvidia_hosts

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(max(1, self.max_retries + 1)):
            try:
                url = f"{self.base_url}/{endpoint.lstrip('/')}"
                if self._external_client is not None:
                    response = await self._external_client.post(
                        url,
                        headers=self._headers(),
                        json=payload,
                        timeout=self.timeout_seconds,
                    )
                else:
                    async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                        response = await client.post(url, headers=self._headers(), json=payload)
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    body = response.text[:1000]
                    raise NIMError(f"NIM request failed ({response.status_code}): {body}") from exc
                body = response.json()
                if not isinstance(body, dict):
                    raise NIMError("NIM response was not a JSON object")
                if body.get("error"):
                    raise NIMError(json.dumps(body["error"]))
                return body
            except (httpx.HTTPError, NIMError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(min(12.0, 0.75 * (2**attempt)))
        assert last_error is not None
        raise last_error

    async def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 800,
    ) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        response = await self._post("chat/completions", payload)
        choices = response.get("choices") or []
        if not choices:
            raise NIMError("NIM chat response contained no choices")
        content = choices[0].get("message", {}).get("content")
        if isinstance(content, list):
            content = "".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in content)
        if not isinstance(content, str) or not content.strip():
            raise NIMError("NIM chat response contained no text")
        return content.strip()

    async def embeddings(
        self,
        *,
        model: str,
        texts: list[str],
        input_type: str = "passage",
        truncate: str = "END",
    ) -> list[list[float]]:
        payload = {
            "model": model,
            "input": texts,
            "encoding_format": "float",
            "input_type": input_type,
            "truncate": truncate,
        }
        response = await self._post("embeddings", payload)
        data = response.get("data") or []
        if len(data) != len(texts):
            raise NIMError(f"Expected {len(texts)} embeddings, received {len(data)}")
        ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
        vectors = [item.get("embedding") for item in ordered]
        if not all(isinstance(vector, list) and vector for vector in vectors):
            raise NIMError("NIM returned an invalid embedding payload")
        return vectors  # type: ignore[return-value]
