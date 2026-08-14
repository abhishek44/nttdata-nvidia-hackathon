from __future__ import annotations

import json
from typing import Any

import httpx

from recallzero.config import settings


class NvidiaNIMClient:
    """Minimal OpenAI-compatible client for NVIDIA NIM LLM + Embedding endpoints."""

    def __init__(self):
        self.llm_url = settings.nvidia_nim_llm_url.rstrip("/")
        self.llm_model = settings.nvidia_nim_llm_model
        self.embed_url = settings.nvidia_nim_embed_url.rstrip("/")
        self.embed_model = settings.nvidia_nim_embed_model
        self.api_key = settings.nvidia_api_key

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_model)

    @property
    def embeddings_enabled(self) -> bool:
        return bool(self.embed_model)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def chat_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> dict[str, Any]:
        if not self.llm_enabled:
            raise RuntimeError("NVIDIA_NIM_LLM_MODEL is not configured")
        payload = {
            "model": self.llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        with httpx.Client(timeout=60.0) as client:
            response = client.post(f"{self.llm_url}/chat/completions", headers=self._headers(), json=payload)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.lower().startswith("json"):
                content = content[4:].lstrip()
        return json.loads(content)

    def embeddings(self, texts: list[str]) -> list[list[float]]:
        if not self.embeddings_enabled:
            raise RuntimeError("NVIDIA_NIM_EMBED_MODEL is not configured")
        payload = {"model": self.embed_model, "input": texts, "encoding_format": "float"}
        with httpx.Client(timeout=120.0) as client:
            response = client.post(f"{self.embed_url}/embeddings", headers=self._headers(), json=payload)
            response.raise_for_status()
            data = response.json()["data"]
        return [item["embedding"] for item in sorted(data, key=lambda x: x.get("index", 0))]
