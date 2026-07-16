"""GET /llm/health、POST /llm/diagnose。"""

import httpx
import respx

from app.config import get_settings
from tests.api.conftest import BASE_URL
from tests.llm.conftest import (
    chat_completion_response,
    sse_stream_response,
    text_chunk,
    tool_call_payload,
)


def _probe_responses() -> list[httpx.Response]:
    """五項探針依序（multi_turn/system_role/native_tools/json_schema/streaming）全數通過的回應。"""
    tool_call = tool_call_payload("call_1", "probe_echo", {"value": "ok"})
    return [
        chat_completion_response(content="SQLAGENT-7731"),
        chat_completion_response(content="SYSMARK-OK"),
        chat_completion_response(content=None, tool_calls=[tool_call], finish_reason="tool_calls"),
        chat_completion_response(content='{"ok": true}'),
        sse_stream_response([text_chunk("哈囉", finish_reason="stop")]),
    ]


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
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions")
        route.side_effect = _probe_responses()
        resp = await client.post("/api/v1/llm/diagnose")

    assert resp.status_code == 200
    body = resp.json()
    profile = body["profile"]
    assert profile == {
        "multi_turn": True,
        "system_role": True,
        "native_tools": True,
        "json_schema": True,
        "streaming": True,
        "probed_at": profile["probed_at"],
    }
    assert profile["probed_at"] is not None
    # 未設定 LLM_FORCE_PROFILE：來源為 probe，profile 即探測結果
    assert body["source"] == "probe"
    assert body["probed"] == profile

    # 探測結果已持久化：health 端點回傳的 profile 應反映最新一次 diagnose 結果
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(return_value=chat_completion_response(content="pong"))
        health_resp = await client.get("/api/v1/llm/health")

    assert health_resp.json()["profile"] == profile


async def test_diagnose_source_forced_when_force_profile_set(client, monkeypatch):
    """設定 LLM_FORCE_PROFILE 時：diagnose 仍量測真實能力（probed），但 profile=forced。"""
    monkeypatch.setenv("LLM_FORCE_PROFILE", '{"native_tools": false, "streaming": false}')
    get_settings.cache_clear()

    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions")
        route.side_effect = _probe_responses()
        resp = await client.post("/api/v1/llm/diagnose")

    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "forced"
    # forced profile 生效：native_tools/streaming 被覆蓋為 False，其餘沿用預設 True
    assert body["profile"]["native_tools"] is False
    assert body["profile"]["streaming"] is False
    assert body["profile"]["multi_turn"] is True
    # probed 仍反映真實量測（全數通過），供比對平台是否已改變
    assert body["probed"]["native_tools"] is True
    assert body["probed"]["streaming"] is True
    # 持久化的是真實探測結果（probed），非 forced：health 回傳 probed
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(return_value=chat_completion_response(content="pong"))
        health_resp = await client.get("/api/v1/llm/health")

    assert health_resp.json()["profile"] == body["probed"]
