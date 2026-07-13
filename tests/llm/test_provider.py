"""LLMProvider：標準多輪路徑、429/5xx 重試、串流分塊、structured output 重試測試。"""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from pydantic import BaseModel

from app.config import Settings
from app.llm.capabilities import CapabilityProfile
from app.llm.errors import LLMError
from app.llm.provider import LLMProvider
from tests.llm.conftest import (
    BASE_URL,
    chat_completion_response,
    sse_stream_response,
    text_chunk,
    tool_call_payload,
    usage_only_chunk,
)


def make_provider(**overrides) -> LLMProvider:
    kwargs = dict(base_url=BASE_URL, api_key="test-key", model="test-model", verify=False)
    kwargs.update(overrides)
    return LLMProvider(**kwargs)


async def test_standard_multi_turn_chat_sends_messages_array_as_is():
    messages = [
        {"role": "system", "content": "你是助理"},
        {"role": "user", "content": "第一句"},
        {"role": "assistant", "content": "回覆"},
        {"role": "user", "content": "第二句"},
    ]
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions").mock(
            return_value=chat_completion_response(content="哈囉")
        )
        provider = make_provider()
        result = await provider.chat(messages)

    assert result.text == "哈囉"
    assert result.tool_calls == []
    assert result.usage.total_tokens == 2
    sent_body = json.loads(route.calls[0].request.content)
    assert sent_body["messages"] == messages  # 標準多輪：不攤平，原樣送出
    assert "tools" not in sent_body
    assert "response_format" not in sent_body


async def test_native_tool_calls_returned():
    tool_call = tool_call_payload("call_1", "get_schema", {"table": "users"})
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(
                content=None, tool_calls=[tool_call], finish_reason="tool_calls"
            )
        )
        provider = make_provider()
        result = await provider.chat(
            [{"role": "user", "content": "查一下 users 表"}],
            tools=[{"type": "function", "function": {"name": "get_schema", "parameters": {}}}],
        )

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "get_schema"
    assert result.tool_calls[0].arguments == {"table": "users"}


async def test_429_retries_with_exponential_backoff_then_succeeds():
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions")
        route.side_effect = [
            httpx.Response(429, json={"error": {"message": "rate limited"}}),
            httpx.Response(429, json={"error": {"message": "rate limited"}}),
            chat_completion_response(content="ok"),
        ]
        provider = make_provider()
        with patch("app.llm.provider.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            result = await provider.chat([{"role": "user", "content": "hi"}])

    assert result.text == "ok"
    assert route.call_count == 3
    assert [c.args[0] for c in mock_sleep.call_args_list] == [2.0, 4.0]


async def test_5xx_retries_exhausted_raises_llm_error():
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions").mock(
            return_value=httpx.Response(503, json={"error": {"message": "unavailable"}})
        )
        provider = make_provider()
        with patch("app.llm.provider.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(LLMError):
                await provider.chat([{"role": "user", "content": "hi"}])

    assert route.call_count == 4  # 1 次原始嘗試 + 3 次重試


async def test_connection_error_raises_llm_error():
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(side_effect=httpx.ConnectError("boom"))
        provider = make_provider()
        with pytest.raises(LLMError):
            await provider.chat([{"role": "user", "content": "hi"}])


async def test_client_error_400_raises_without_retry():
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions").mock(
            return_value=httpx.Response(400, json={"error": {"message": "bad request"}})
        )
        provider = make_provider()
        with pytest.raises(LLMError):
            await provider.chat([{"role": "user", "content": "hi"}])

    assert route.call_count == 1  # 4xx（非 429）不重試


async def test_streaming_yields_chunks_and_final_usage():
    chunks = [
        text_chunk("哈"),
        text_chunk("囉"),
        text_chunk("", finish_reason="stop"),
        usage_only_chunk({"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}),
    ]
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(return_value=sse_stream_response(chunks))
        provider = make_provider()
        stream = await provider.chat([{"role": "user", "content": "hi"}], stream=True)
        collected = [c async for c in stream]

    text = "".join(c.delta for c in collected if c.delta)
    assert text == "哈囉"
    assert collected[-1].usage.total_tokens == 3


async def test_streaming_degraded_when_profile_streaming_false():
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions").mock(
            return_value=chat_completion_response(content="整段文字")
        )
        provider = make_provider(profile=CapabilityProfile(streaming=False))
        stream = await provider.chat([{"role": "user", "content": "hi"}], stream=True)
        collected = [c async for c in stream]

    assert len(collected) == 1
    assert collected[0].delta == "整段文字"
    assert collected[0].done is True
    sent_body = json.loads(route.calls[0].request.content)
    assert "stream" not in sent_body  # 降級為非串流呼叫


class _Draft(BaseModel):
    sql: str
    explanation: str


async def test_structured_output_native_success():
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions").mock(
            return_value=chat_completion_response(
                content='{"sql": "SELECT 1", "explanation": "test"}'
            )
        )
        provider = make_provider()
        result = await provider.chat([{"role": "user", "content": "sql?"}], response_model=_Draft)

    assert isinstance(result.parsed, _Draft)
    assert result.parsed.sql == "SELECT 1"
    sent_body = json.loads(route.calls[0].request.content)
    assert sent_body["response_format"]["type"] == "json_schema"


async def test_structured_output_parse_failure_retries_once_then_succeeds():
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions")
        route.side_effect = [
            chat_completion_response(content="這不是 JSON"),
            chat_completion_response(content='{"sql": "SELECT 1", "explanation": "ok"}'),
        ]
        provider = make_provider()
        result = await provider.chat([{"role": "user", "content": "sql?"}], response_model=_Draft)

    assert route.call_count == 2
    assert result.parsed.sql == "SELECT 1"


async def test_structured_output_parse_failure_twice_raises_llm_error():
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions").mock(
            return_value=chat_completion_response(content="還是不是 JSON")
        )
        provider = make_provider()
        with pytest.raises(LLMError):
            await provider.chat([{"role": "user", "content": "sql?"}], response_model=_Draft)

    assert route.call_count == 2  # 原始一次 + 自動重試一次


async def test_structured_output_strips_markdown_fence():
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(
                content='```json\n{"sql": "SELECT 1", "explanation": "ok"}\n```'
            )
        )
        provider = make_provider()
        result = await provider.chat([{"role": "user", "content": "sql?"}], response_model=_Draft)

    assert result.parsed.sql == "SELECT 1"


def test_from_settings_reads_llm_config():
    settings = Settings(
        llm_base_url=BASE_URL, llm_api_key="k", llm_model="m", llm_verify=False, llm_timeout=5.0
    )
    provider = LLMProvider.from_settings(settings)
    assert provider.model == "m"
