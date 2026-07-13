"""能力探測：五項探針各自的判定邏輯，透過 respx mock gateway 回應驗證。"""

import respx

from app.llm.capabilities import (
    CapabilityProfile,
    probe_all,
    probe_json_schema,
    probe_multi_turn,
    probe_native_tools,
    probe_streaming,
    probe_system_role,
)
from app.llm.provider import LLMProvider
from tests.llm.conftest import (
    BASE_URL,
    chat_completion_response,
    sse_stream_response,
    text_chunk,
    tool_call_payload,
)


def make_provider() -> LLMProvider:
    return LLMProvider(base_url=BASE_URL, api_key="k", model="m", verify=False)


async def test_probe_multi_turn_true_when_codeword_recalled():
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(content="SQLAGENT-7731")
        )
        assert await probe_multi_turn(make_provider()) is True


async def test_probe_multi_turn_false_when_codeword_not_recalled():
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(content="我不記得了")
        )
        assert await probe_multi_turn(make_provider()) is False


async def test_probe_system_role_true_when_marker_returned():
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(content="SYSMARK-OK")
        )
        assert await probe_system_role(make_provider()) is True


async def test_probe_system_role_false_when_marker_missing():
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(content="今天天氣晴朗")
        )
        assert await probe_system_role(make_provider()) is False


async def test_probe_native_tools_true_when_tool_calls_returned():
    tool_call = tool_call_payload("call_1", "probe_echo", {"value": "ok"})
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(
                content=None, tool_calls=[tool_call], finish_reason="tool_calls"
            )
        )
        assert await probe_native_tools(make_provider()) is True


async def test_probe_native_tools_false_when_no_tool_calls():
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(content="我不會用工具")
        )
        assert await probe_native_tools(make_provider()) is False


async def test_probe_json_schema_true_when_valid_json_returned():
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(content='{"ok": true}')
        )
        assert await probe_json_schema(make_provider()) is True


async def test_probe_json_schema_false_when_response_not_parseable():
    with respx.mock(base_url=BASE_URL) as mock:
        # 探測用的自動重試也會呼叫一次，兩次都回傳無法解析的文字
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(content="不是 JSON")
        )
        assert await probe_json_schema(make_provider()) is False


async def test_probe_streaming_true_when_delta_received():
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=sse_stream_response([text_chunk("哈囉", finish_reason="stop")])
        )
        assert await probe_streaming(make_provider()) is True


async def test_probe_streaming_false_when_no_delta():
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=sse_stream_response([text_chunk("", finish_reason="stop")])
        )
        assert await probe_streaming(make_provider()) is False


async def test_probe_all_returns_full_profile_with_probed_at():
    tool_call = tool_call_payload("call_1", "probe_echo", {"value": "ok"})
    responses = [
        chat_completion_response(content="SQLAGENT-7731"),  # multi_turn
        chat_completion_response(content="SYSMARK-OK"),  # system_role
        chat_completion_response(
            content=None, tool_calls=[tool_call], finish_reason="tool_calls"
        ),  # native_tools
        chat_completion_response(content='{"ok": true}'),  # json_schema
        sse_stream_response([text_chunk("哈囉", finish_reason="stop")]),  # streaming
    ]
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions")
        route.side_effect = responses
        profile = await probe_all(make_provider())

    assert profile == CapabilityProfile(
        multi_turn=True,
        system_role=True,
        native_tools=True,
        json_schema=True,
        streaming=True,
        probed_at=profile.probed_at,
    )
    assert profile.probed_at is not None
