"""Pensieve 自訂 API adapter 的 request/response 契約。"""

import json

import httpx
import pytest
import respx
from pydantic import BaseModel

from app.llm.errors import LLMError
from app.llm.pensieve import PENSIEVE_PROFILE, PensieveProvider


async def test_pensieve_payload_and_result_parsing():
    with respx.mock(base_url="http://pensieve.test") as mock:
        route = mock.post("/api").mock(
            return_value=httpx.Response(200, json={"isSuccess": True, "Result": "回答"})
        )
        provider = PensieveProvider(
            url="http://pensieve.test/api",
            token="token-1",
            empno="E123",
            building="option",
            verify=False,
            trust_env=False,
            timeout=5,
        )
        result = await provider.chat(
            [
                {"role": "system", "content": "系統指示"},
                {"role": "user", "content": "第一問"},
                {"role": "assistant", "content": "第一答"},
                {"role": "user", "content": "第二問"},
            ]
        )

    assert result.text == "回答"
    payload = json.loads(route.calls[0].request.content)
    assert payload["token"] == "token-1"
    assert payload["empno"] == "E123"
    assert payload["variables"]["building"] == "option"
    assert payload["variables"]["other_system_prompt"] == "系統指示"
    assert "第一問" in payload["variables"]["other_human_prompt"]
    assert "第一答" in payload["variables"]["other_human_prompt"]
    assert "第二問" in payload["variables"]["other_human_prompt"]


async def test_pensieve_emulates_tools():
    tool_json = '{"tool_call":{"name":"get_schema","arguments":{"db":"CIM"}}}'
    with respx.mock(base_url="http://pensieve.test") as mock:
        route = mock.post("/api").mock(
            return_value=httpx.Response(200, json={"isSuccess": True, "Result": tool_json})
        )
        provider = PensieveProvider(
            url="http://pensieve.test/api", token="t", empno="e", profile=PENSIEVE_PROFILE
        )
        result = await provider.chat(
            [{"role": "user", "content": "查結構"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "get_schema",
                        "description": "取得結構",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        )

    assert result.tool_calls[0].name == "get_schema"
    assert result.tool_calls[0].arguments == {"db": "CIM"}
    payload = json.loads(route.calls[0].request.content)
    assert "get_schema" in payload["variables"]["other_human_prompt"]


class _Answer(BaseModel):
    answer: str


async def test_pensieve_emulates_structured_output_and_streaming():
    with respx.mock(base_url="http://pensieve.test") as mock:
        route = mock.post("/api")
        route.side_effect = [
            httpx.Response(
                200, json={"isSuccess": True, "Result": '{"answer":"ok"}'}
            ),
            httpx.Response(200, json={"isSuccess": True, "Result": "完整回答"}),
        ]
        provider = PensieveProvider(url="http://pensieve.test/api", token="t", empno="e")
        structured = await provider.chat(
            [{"role": "user", "content": "回答"}], response_model=_Answer
        )
        stream = await provider.chat(
            [{"role": "user", "content": "回答"}], stream=True
        )
        chunks = [chunk async for chunk in stream]

    assert structured.parsed == _Answer(answer="ok")
    assert "answer" in json.loads(route.calls[0].request.content)["variables"][
        "other_human_prompt"
    ]
    assert [(chunk.delta, chunk.done) for chunk in chunks] == [("完整回答", True)]


async def test_pensieve_rejects_unsuccessful_envelope():
    with respx.mock(base_url="http://pensieve.test") as mock:
        mock.post("/api").mock(
            return_value=httpx.Response(200, json={"isSuccess": False, "message": "denied"})
        )
        provider = PensieveProvider(url="http://pensieve.test/api", token="t", empno="e")
        with pytest.raises(LLMError, match="Pensieve API 回傳失敗"):
            await provider.chat([{"role": "user", "content": "ping"}])
