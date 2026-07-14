"""POST /sessions/{id}/messages（SSE 模式）：delta* → turn_done 事件順序。"""

import json
import re

import respx

from tests.api.conftest import BASE_URL, interview_turn_payload, sample_table
from tests.llm.conftest import chat_completion_response

_EVENT_RE = re.compile(r"event: (\w+)\ndata: (.+)\n\n")


async def test_sse_stream_emits_delta_events_then_turn_done(client):
    session = (await client.post("/api/v1/sessions", json={})).json()
    long_reply = "這是一段夠長的回覆文字，用來確保會被切成不只一個 delta 增量塊送出。" * 2

    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(content=interview_turn_payload(long_reply))
        )
        resp = await client.post(
            f"/api/v1/sessions/{session['id']}/messages",
            json={"content": "我想要一個使用者資料表"},
            headers={"Accept": "text/event-stream"},
        )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _EVENT_RE.findall(resp.text)
    assert len(events) >= 2  # 至少一個 delta + turn_done
    names = [name for name, _ in events]
    assert names[-1] == "turn_done"
    assert all(name == "delta" for name in names[:-1])

    # delta 依序串起來應等於完整回覆文字
    delta_text = "".join(json.loads(data)["delta"] for name, data in events if name == "delta")
    assert delta_text == long_reply

    turn_done_data = json.loads(events[-1][1])
    assert turn_done_data["tables_ready"] is False
    assert turn_done_data["reply"] == long_reply


async def test_sse_stream_turn_done_carries_tables_ready(client):
    session = (await client.post("/api/v1/sessions", json={})).json()
    tables = [sample_table("orders")]

    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(
                content=interview_turn_payload("設計完成", tables=tables, summary=["訂單表"])
            )
        )
        resp = await client.post(
            f"/api/v1/sessions/{session['id']}/messages",
            json={"content": "我需要一張訂單表"},
            headers={"Accept": "text/event-stream"},
        )

    events = _EVENT_RE.findall(resp.text)
    turn_done_data = json.loads(events[-1][1])
    assert turn_done_data["tables_ready"] is True
    assert turn_done_data["tables"][0]["table_name"] == "orders"
    assert turn_done_data["summary"] == ["訂單表"]
