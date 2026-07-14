"""GET /llm/health、POST /llm/diagnose。"""

import httpx
import respx

from tests.api.conftest import BASE_URL
from tests.llm.conftest import (
    chat_completion_response,
    sse_stream_response,
    text_chunk,
    tool_call_payload,
)


async def test_health_ok_true_when_ping_succeeds(client):
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(return_value=chat_completion_response(content="pong"))
        resp = await client.get("/api/v1/llm/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["model"] == "test-model"
    # 尚未跑過 diagnose 時，profile 回傳全為 True 的預設值
    assert body["profile"]["multi_turn"] is True


async def test_health_ok_false_when_ping_fails(client):
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(side_effect=httpx.ConnectError("boom"))
        resp = await client.get("/api/v1/llm/health")

    assert resp.status_code == 200
    assert resp.json()["ok"] is False


async def test_diagnose_probes_and_persists_profile(client):
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
        resp = await client.post("/api/v1/llm/diagnose")

    assert resp.status_code == 200
    profile = resp.json()["profile"]
    assert profile == {
        "multi_turn": True,
        "system_role": True,
        "native_tools": True,
        "json_schema": True,
        "streaming": True,
        "probed_at": profile["probed_at"],
    }
    assert profile["probed_at"] is not None

    # 探測結果已持久化：health 端點回傳的 profile 應反映最新一次 diagnose 結果
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(return_value=chat_completion_response(content="pong"))
        health_resp = await client.get("/api/v1/llm/health")

    assert health_resp.json()["profile"] == profile
