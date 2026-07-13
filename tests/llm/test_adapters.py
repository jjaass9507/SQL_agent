"""降級轉接層：純函式的轉接邏輯（無 HTTP），以及透過 respx 驗證 LLMProvider
在各項能力缺失時，實際送出的請求與解析結果符合預期（對應計畫書 §4-3 五項降級）。
"""

import json

import respx
from pydantic import BaseModel

from app.llm.adapters import AdaptedRequest, apply, parse_tool_call_from_text
from app.llm.capabilities import CapabilityProfile
from app.llm.provider import LLMProvider
from tests.llm.conftest import BASE_URL, chat_completion_response

_TOOLS = [
    {
        "type": "function",
        "function": {"name": "get_schema", "description": "查表結構", "parameters": {}},
    }
]


class _Draft(BaseModel):
    sql: str


# -- 純函式測試：apply() 的轉接邏輯 ------------------------------------------


def test_apply_all_supported_is_noop():
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    adapted = apply(
        messages,
        _TOOLS,
        _Draft,
        multi_turn=True,
        system_role=True,
        native_tools=True,
        json_schema=True,
    )
    assert adapted == AdaptedRequest(
        messages=messages,
        api_tools=_TOOLS,
        api_response_model=_Draft,
        emulate_tools=False,
        emulate_schema=None,
    )


def test_apply_native_tools_off_injects_catalog_and_drops_api_tools():
    messages = [{"role": "user", "content": "查一下 users 表"}]
    adapted = apply(
        messages,
        _TOOLS,
        None,
        multi_turn=True,
        system_role=True,
        native_tools=False,
        json_schema=True,
    )

    assert adapted.api_tools is None
    assert adapted.emulate_tools is True
    assert "get_schema" in adapted.messages[0]["content"]
    assert "查一下 users 表" in adapted.messages[0]["content"]


def test_apply_json_schema_off_injects_schema_and_drops_response_format():
    messages = [{"role": "user", "content": "給我 SQL"}]
    adapted = apply(
        messages,
        None,
        _Draft,
        multi_turn=True,
        system_role=True,
        native_tools=True,
        json_schema=False,
    )

    assert adapted.api_response_model is None
    assert adapted.emulate_schema is _Draft
    assert "sql" in adapted.messages[0]["content"]


def test_apply_system_role_off_merges_into_first_user_message():
    messages = [{"role": "system", "content": "你是助理"}, {"role": "user", "content": "你好"}]
    adapted = apply(
        messages,
        None,
        None,
        multi_turn=True,
        system_role=False,
        native_tools=True,
        json_schema=True,
    )

    assert len(adapted.messages) == 1
    assert adapted.messages[0]["role"] == "user"
    assert adapted.messages[0]["content"] == "你是助理\n\n你好"


def test_apply_multi_turn_off_flattens_entire_history():
    messages = [
        {"role": "system", "content": "你是助理"},
        {"role": "user", "content": "第一句"},
        {"role": "assistant", "content": "回覆"},
        {"role": "user", "content": "第二句"},
    ]
    adapted = apply(
        messages,
        None,
        None,
        multi_turn=False,
        system_role=True,
        native_tools=True,
        json_schema=True,
    )

    assert len(adapted.messages) == 1
    assert adapted.messages[0]["role"] == "user"
    for fragment in ("你是助理", "第一句", "回覆", "第二句"):
        assert fragment in adapted.messages[0]["content"]


def test_apply_system_role_and_multi_turn_off_applies_system_role_first():
    """system_role 降級應先套用，multi_turn 攤平時不應出現重複的系統指示標籤。"""
    messages = [{"role": "system", "content": "你是助理"}, {"role": "user", "content": "你好"}]
    adapted = apply(
        messages,
        None,
        None,
        multi_turn=False,
        system_role=False,
        native_tools=True,
        json_schema=True,
    )

    assert len(adapted.messages) == 1
    content = adapted.messages[0]["content"]
    assert content.count("你是助理") == 1
    assert "[系統指示]" not in content  # system 已於前一步併入 user，不會被貼上系統標籤


def test_parse_tool_call_from_text_valid_json():
    text = '{"tool_call": {"name": "get_schema", "arguments": {"table": "users"}}}'
    call = parse_tool_call_from_text(text)
    assert call is not None
    assert call.name == "get_schema"
    assert call.arguments == {"table": "users"}


def test_parse_tool_call_from_text_strips_markdown_fence():
    text = '```json\n{"tool_call": {"name": "get_schema", "arguments": {}}}\n```'
    call = parse_tool_call_from_text(text)
    assert call is not None
    assert call.name == "get_schema"


def test_parse_tool_call_from_text_invalid_returns_none():
    assert parse_tool_call_from_text("我不需要呼叫工具") is None
    assert parse_tool_call_from_text('{"foo": "bar"}') is None
    assert parse_tool_call_from_text("") is None


# -- 端到端測試：透過 respx 驗證 LLMProvider 在能力缺失時的實際降級行為 --------


async def test_provider_native_tools_degraded_end_to_end():
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions").mock(
            return_value=chat_completion_response(
                content='{"tool_call": {"name": "get_schema", "arguments": {"table": "orders"}}}'
            )
        )
        provider = LLMProvider(
            base_url=BASE_URL,
            api_key="k",
            model="m",
            verify=False,
            profile=CapabilityProfile(native_tools=False),
        )
        result = await provider.chat([{"role": "user", "content": "查 orders 表"}], tools=_TOOLS)

    sent_body = json.loads(route.calls[0].request.content)
    assert "tools" not in sent_body
    assert result.tool_calls[0].name == "get_schema"
    assert result.tool_calls[0].arguments == {"table": "orders"}


async def test_provider_json_schema_degraded_end_to_end():
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions").mock(
            return_value=chat_completion_response(content='{"sql": "SELECT 1"}')
        )
        provider = LLMProvider(
            base_url=BASE_URL,
            api_key="k",
            model="m",
            verify=False,
            profile=CapabilityProfile(json_schema=False),
        )
        result = await provider.chat(
            [{"role": "user", "content": "給我 SQL"}], response_model=_Draft
        )

    sent_body = json.loads(route.calls[0].request.content)
    assert "response_format" not in sent_body
    assert result.parsed.sql == "SELECT 1"


async def test_provider_system_role_degraded_end_to_end():
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions").mock(
            return_value=chat_completion_response(content="收到")
        )
        provider = LLMProvider(
            base_url=BASE_URL,
            api_key="k",
            model="m",
            verify=False,
            profile=CapabilityProfile(system_role=False),
        )
        await provider.chat(
            [{"role": "system", "content": "你是助理"}, {"role": "user", "content": "你好"}]
        )

    sent_body = json.loads(route.calls[0].request.content)
    assert all(m["role"] != "system" for m in sent_body["messages"])
    assert sent_body["messages"][0]["content"] == "你是助理\n\n你好"


async def test_provider_multi_turn_degraded_end_to_end():
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions").mock(
            return_value=chat_completion_response(content="收到")
        )
        provider = LLMProvider(
            base_url=BASE_URL,
            api_key="k",
            model="m",
            verify=False,
            profile=CapabilityProfile(multi_turn=False),
        )
        await provider.chat(
            [
                {"role": "user", "content": "第一句"},
                {"role": "assistant", "content": "回覆"},
                {"role": "user", "content": "第二句"},
            ]
        )

    sent_body = json.loads(route.calls[0].request.content)
    assert len(sent_body["messages"]) == 1
    assert sent_body["messages"][0]["role"] == "user"
