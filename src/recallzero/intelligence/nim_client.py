from __future__ import annotations

import asyncio
import email.utils
import json
import logging
import random
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


class NIMError(RuntimeError):
    pass


class NIMTransientError(NIMError):
    """A retryable hosted/local NIM failure."""


class NIMRateLimitError(NIMTransientError):
    """NIM returned HTTP 429 after the configured retry budget."""


class NIMClient:
    """Small OpenAI-compatible client for hosted NVIDIA APIs or a local NIM.

    Retry behavior is intentionally conservative for hosted NIMs: rate limits and
    transient gateway/service failures are retried with Retry-After awareness,
    exponential backoff, and jitter. Permanent client errors are not retried.
    """

    RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str,
        timeout_seconds: float = 90,
        max_retries: int = 7,
        retry_base_delay_seconds: float = 1.5,
        retry_max_delay_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_base_delay_seconds = retry_base_delay_seconds
        self.retry_max_delay_seconds = retry_max_delay_seconds
        self._external_client = client

    @property
    def configured(self) -> bool:
        if self.api_key:
            return True
        parsed = urlparse(self.base_url)
        hosted_nvidia_hosts = {"integrate.api.nvidia.com", "api.nvidia.com"}
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and parsed.hostname not in hosted_nvidia_hosts

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if not value:
            return None
        value = value.strip()
        try:
            return max(0.0, float(value))
        except ValueError:
            pass
        try:
            parsed = email.utils.parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return max(0.0, (parsed - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None

    def _backoff_seconds(self, attempt: int, response: httpx.Response | None = None) -> float:
        retry_after = self._retry_after_seconds(response) if response is not None else None
        if retry_after is not None:
            base = min(self.retry_max_delay_seconds, retry_after)
        else:
            base = min(self.retry_max_delay_seconds, self.retry_base_delay_seconds * (2**attempt))
        # Small jitter prevents synchronized retry storms when multiple extraction workers are active.
        return min(self.retry_max_delay_seconds, base + random.uniform(0.0, min(1.0, base * 0.2)))

    async def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        attempts = max(1, self.max_retries + 1)

        for attempt in range(attempts):
            response: httpx.Response | None = None
            try:
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

                if response.status_code >= 400:
                    body = response.text[:1000]
                    if response.status_code in self.RETRYABLE_STATUS:
                        if response.status_code == 429:
                            last_error = NIMRateLimitError(f"NIM request failed (429): {body}")
                        else:
                            last_error = NIMTransientError(
                                f"NIM request failed ({response.status_code}): {body}"
                            )
                        if attempt >= attempts - 1:
                            raise last_error
                        delay = self._backoff_seconds(attempt, response)
                        logger.warning(
                            "Transient NIM HTTP %s; retrying in %.1fs (attempt %s/%s)",
                            response.status_code,
                            delay,
                            attempt + 2,
                            attempts,
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise NIMError(f"NIM request failed ({response.status_code}): {body}")

                body = response.json()
                if not isinstance(body, dict):
                    raise NIMError("NIM response was not a JSON object")
                if body.get("error"):
                    raise NIMError(json.dumps(body["error"]))
                return body

            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = NIMTransientError(str(exc))
                if attempt >= attempts - 1:
                    raise last_error from exc
                delay = self._backoff_seconds(attempt, response)
                logger.warning(
                    "Transient NIM network failure; retrying in %.1fs (attempt %s/%s): %s",
                    delay,
                    attempt + 2,
                    attempts,
                    exc,
                )
                await asyncio.sleep(delay)
            except httpx.HTTPError as exc:
                # Unexpected HTTP client failures are retried, but ordinary 4xx responses
                # have already been classified above and are not routed here.
                last_error = NIMTransientError(str(exc))
                if attempt >= attempts - 1:
                    raise last_error from exc
                delay = self._backoff_seconds(attempt, response)
                await asyncio.sleep(delay)

        assert last_error is not None
        raise last_error

    async def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 800,
        guided_json: dict[str, Any] | None = None,
        disable_thinking: bool = False,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if guided_json is not None:
            payload["guided_json"] = guided_json
        if disable_thinking:
            # This is an inference request field, not a NeMo Agent Toolkit YAML key.
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        response = await self._post("chat/completions", payload)
        choices = response.get("choices") or []
        if not choices:
            raise NIMError("NIM chat response contained no choices")
        choice = choices[0]
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", "")) if isinstance(part, dict) else str(part)
                for part in content
            )
        if not isinstance(content, str) or not content.strip():
            reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
            reasoning_chars = len(str(reasoning))
            finish_reason = choice.get("finish_reason")
            hint = ""
            if reasoning_chars:
                hint = (
                    " Reasoning-capable models may consume the completion budget before producing visible JSON; "
                    "disable thinking for extraction or increase max_tokens."
                )
            raise NIMError(
                "NIM chat response contained no visible text "
                f"(finish_reason={finish_reason}, reasoning_chars={reasoning_chars}).{hint}"
            )
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
