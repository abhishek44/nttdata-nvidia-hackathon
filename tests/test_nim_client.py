from recallzero.intelligence import NIMClient


def test_nim_configuration_accepts_key_or_local_endpoint() -> None:
    hosted = NIMClient(api_key="nvapi-test", base_url="https://integrate.api.nvidia.com/v1")
    missing_key = NIMClient(api_key=None, base_url="https://integrate.api.nvidia.com/v1")
    local = NIMClient(api_key=None, base_url="http://127.0.0.1:8000/v1")
    lan = NIMClient(api_key=None, base_url="http://192.168.1.20:8000/v1")

    assert hosted.configured is True
    assert missing_key.configured is False
    assert local.configured is True
    assert lan.configured is True

import json

import httpx
import pytest


@pytest.mark.asyncio
async def test_chat_completion_sends_guided_json_and_disables_thinking() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"system":"POWER TRAIN"}'},
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = NIMClient(
            api_key="nvapi-test",
            base_url="https://integrate.api.nvidia.com/v1",
            max_retries=0,
            client=http_client,
        )
        result = await client.chat_completion(
            model="nvidia/nemotron-3.5-lightning-30b-a3b",
            messages=[{"role": "user", "content": "extract"}],
            guided_json={"type": "object", "properties": {"system": {"type": "string"}}},
            disable_thinking=True,
        )

    assert result == '{"system":"POWER TRAIN"}'
    assert captured["guided_json"]["type"] == "object"
    assert captured["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.asyncio
async def test_chat_completion_empty_visible_content_has_reasoning_diagnostic() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": "", "reasoning_content": "thinking" * 20},
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = NIMClient(
            api_key="nvapi-test",
            base_url="https://integrate.api.nvidia.com/v1",
            max_retries=0,
            client=http_client,
        )
        with pytest.raises(Exception, match="Reasoning-capable models"):
            await client.chat_completion(
                model="nvidia/nemotron-3.5-lightning-30b-a3b",
                messages=[{"role": "user", "content": "extract"}],
            )
