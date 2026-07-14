"""app/services/agent_service.py 的核心 ReAct 工具迴圈測試。

respx 攔截 `LLMProvider` 底層的 HTTP 呼叫（同 tests/llm/conftest.py 的 mock
helper），每個測試用 `make_provider()` 明確建構一個 `LLMProvider` 傳入
`run_agent_turn_stream(..., provider=provider)`，不需要真實 gateway。
"""

import json
import uuid

import respx

from app.repos import messages as messages_repo
from app.repos import settings as settings_repo
from app.services import agent_service
from tests.agent.conftest import (
    BASE_URL,
    chat_response,
    install_fake_psycopg2,
    make_provider,
    seed_business_db,
    tool_call,
)


async def _collect(db_session, message, db_name=None, provider=None):
    events = []
    async for event in agent_service.run_agent_turn_stream(
        db_session, message, db_name, provider=provider or make_provider()
    ):
        events.append(event)
    return events


def _turn_done(events: list[dict]) -> dict:
    assert events[-1]["event"] == "turn_done"
    return events[-1]["data"]


async def _agent_session_id(db_session) -> uuid.UUID:
    setting = await settings_repo.get_setting(db_session, "agent_session_id")
    return uuid.UUID(setting.value_json)


# -- 多步工具鏈 ----------------------------------------------------------------


async def test_multi_step_tool_chain(db_session):
    await seed_business_db(db_session, "shop", "sqlite://")

    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions")
        route.side_effect = [
            chat_response(tool_calls=[tool_call("c1", "list_databases", {})]),
            chat_response(tool_calls=[tool_call("c2", "run_query", {"sql": "SELECT 1 AS a"})]),
            chat_response(content="以上是查詢結果。"),
        ]
        events = await _collect(db_session, "幫我看一下有哪些資料庫，然後查一筆資料")

    assert route.call_count == 3
    kinds = [e["event"] for e in events]
    assert kinds == [
        "tool_call", "tool_result",
        "tool_call", "tool_result",
        "delta", "turn_done",
    ]
    data = _turn_done(events)
    assert data["reply"] == "以上是查詢結果。"
    assert len(data["steps"]) == 2
    assert data["steps"][0]["tool"] == "list_databases"
    assert data["steps"][1]["tool"] == "run_query"
    assert data["proposal"] is None
    assert data["design_request"] is None

    session_id = await _agent_session_id(db_session)
    history = await messages_repo.list_messages(db_session, session_id)
    # user + (tool_call+tool_result) x2 + 最終 ai 文字回覆 = 6 則
    assert len(history) == 6
    assert history[0].role == "user"
    assert history[-1].role == "ai"
    assert history[-1].content == "以上是查詢結果。"


# -- propose_ddl 為 terminal 工具：呼叫即結束，不再打 LLM ---------------------


async def test_propose_ddl_is_terminal(db_session, monkeypatch):
    install_fake_psycopg2(monkeypatch)
    await seed_business_db(db_session, "shop", "postgresql://x/y")

    ddl = "CREATE INDEX idx_users_name ON users(name);"
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions")
        route.side_effect = [
            chat_response(tool_calls=[tool_call("c1", "propose_ddl", {"ddl": ddl, "reason": "x"})]),
            # 若迴圈誤呼叫第二次 LLM，這個回應會被消費並讓斷言失敗（call_count 對不上）
            chat_response(content="不應該被呼叫到這裡"),
        ]
        events = await _collect(db_session, "幫 users 表的 name 欄位建立索引")

    assert route.call_count == 1  # propose_ddl 是 terminal：只呼叫一次 LLM
    data = _turn_done(events)
    assert data["proposal"]["status"] == "pending"
    assert data["proposal"]["dry_run_ok"] is True
    assert "proposal_id" in data["proposal"]
    assert "已提交結構變更提案" in data["reply"]


async def test_propose_ddl_failure_synthesizes_reply_without_extra_llm_call(db_session):
    # 沒有設定任何業務資料庫 → propose_ddl 的 handler 直接回錯誤，一樣是 terminal。
    ddl_args = {"ddl": "CREATE INDEX i ON t(a);"}
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions")
        route.side_effect = [chat_response(tool_calls=[tool_call("c1", "propose_ddl", ddl_args)])]
        events = await _collect(db_session, "幫我加個索引")

    assert route.call_count == 1
    data = _turn_done(events)
    assert data["proposal"] is None
    assert "提案未成立" in data["reply"]


# -- 工具錯誤時的自我修正（error 回饋給 LLM，下一輪修正後成功）---------------


async def test_tool_error_is_fed_back_for_self_correction(db_session):
    await seed_business_db(db_session, "shop", "sqlite://")

    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions")
        route.side_effect = [
            # 第一次呼叫工具缺少必要參數 sql
            chat_response(tool_calls=[tool_call("c1", "run_query", {})]),
            chat_response(tool_calls=[tool_call("c2", "run_query", {"sql": "SELECT 1 AS a"})]),
            chat_response(content="修正後查詢成功。"),
        ]
        events = await _collect(db_session, "查一下資料")

    data = _turn_done(events)
    assert len(data["steps"]) == 2
    assert "錯誤" in data["steps"][0]["result_summary"]
    assert "1 筆結果" == data["steps"][1]["result_summary"]

    # 第二次呼叫 LLM 時，messages 裡應包含第一次工具錯誤的 tool 訊息，讓模型能自我修正。
    second_call_body = json.loads(route.calls[1].request.content)
    tool_messages = [m for m in second_call_body["messages"] if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert "缺少必要參數" in tool_messages[0]["content"]


async def test_run_query_guardrail_blocks_dml_inside_loop(db_session):
    await seed_business_db(db_session, "shop", "sqlite://")

    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").side_effect = [
            chat_response(tool_calls=[tool_call("c1", "run_query", {"sql": "DELETE FROM t"})]),
            chat_response(content="無法執行寫入操作。"),
        ]
        events = await _collect(db_session, "幫我刪除資料")

    data = _turn_done(events)
    assert "錯誤" in data["steps"][0]["result_summary"]
    assert data["reply"] == "無法執行寫入操作。"


# -- transcript 跨回合重建 -----------------------------------------------------


async def test_transcript_rebuilds_across_turns(db_session):
    await seed_business_db(db_session, "shop", "sqlite://")

    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions")
        route.side_effect = [
            chat_response(tool_calls=[tool_call("c1", "list_databases", {})]),
            chat_response(content="第一輪回覆"),
            chat_response(content="第二輪回覆"),
        ]
        await _collect(db_session, "第一輪使用者訊息")
        await _collect(db_session, "第二輪使用者訊息")

    assert route.call_count == 3

    # 同一個全域 agent session，第二輪應重用同一個 id（只建立一次）。
    setting = await settings_repo.get_setting(db_session, "agent_session_id")
    assert setting is not None

    third_call_body = json.loads(route.calls[2].request.content)
    roles_and_content = [
        (m["role"], m.get("content"), "tool_calls" in m) for m in third_call_body["messages"]
    ]
    # system, user(第一輪), assistant(tool_calls), tool(observation),
    # assistant(第一輪回覆), user(第二輪) —— 重建後應完整保留這個順序與型態。
    assert roles_and_content[0][0] == "system"
    assert roles_and_content[1] == ("user", "第一輪使用者訊息", False)
    assert roles_and_content[2][0] == "assistant"
    assert roles_and_content[2][2] is True  # 帶 tool_calls
    assert roles_and_content[3][0] == "tool"
    assert roles_and_content[4] == ("assistant", "第一輪回覆", False)
    assert roles_and_content[5] == ("user", "第二輪使用者訊息", False)


# -- design_request 抽取 -------------------------------------------------------


async def test_design_request_extracted_from_final_reply(db_session):
    raw = (
        "好的，我了解你的需求。\n\n"
        "[[DESIGN_REQUEST]]使用者想建立訂單資料表[[/DESIGN_REQUEST]]"
    )
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(return_value=chat_response(content=raw))
        events = await _collect(db_session, "我想新增一個訂單表")

    data = _turn_done(events)
    assert data["design_request"] == "使用者想建立訂單資料表"
    assert "DESIGN_REQUEST" not in data["reply"]
    assert data["reply"] == "好的，我了解你的需求。"


# -- 上限 8 步 ------------------------------------------------------------------


async def test_max_steps_limit_stops_without_extra_llm_call(db_session):
    await seed_business_db(db_session, "shop", "sqlite://")

    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions")
        route.side_effect = [
            chat_response(tool_calls=[tool_call(f"c{i}", "list_databases", {})]) for i in range(8)
        ]
        events = await _collect(db_session, "一直查一直查")

    assert route.call_count == 8  # MAX_STEPS=8，恰好用完不再多打一次 LLM
    data = _turn_done(events)
    assert data["reply"] == agent_service._MAX_STEPS_REPLY
    assert len(data["steps"]) == 8


# -- observation / messages 截斷 -----------------------------------------------


def test_cap_rows_truncates_to_20():
    result = {"rows": [[i] for i in range(50)], "columns": ["a"]}
    capped = agent_service._cap_rows(result)
    assert len(capped["rows"]) == 20
    assert capped["truncated"] is True


def test_cap_rows_noop_when_under_limit():
    result = {"rows": [[1]], "columns": ["a"]}
    capped = agent_service._cap_rows(result)
    assert capped == result


def test_obs_text_truncates_at_4000_chars():
    result = {"rows": [["x" * 100] for _ in range(100)]}
    text = agent_service._obs_text(result)
    assert len(text) <= agent_service.MAX_OBS_CHARS + len("...(截斷)")
    assert text.endswith("...(截斷)")


def test_trim_to_budget_drops_oldest_messages():
    big = "x" * 5_000
    messages = [{"role": "user", "content": big} for _ in range(10)]
    trimmed = agent_service._trim_to_budget(messages)
    total = sum(agent_service._msg_chars(m) for m in trimmed)
    assert total <= agent_service.MAX_MESSAGES_CHARS
    assert len(trimmed) < len(messages)


async def test_large_query_result_is_truncated_when_persisted(db_session):
    await seed_business_db(db_session, "shop", "sqlite://")
    sql = (
        "WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM c WHERE n < 100) "
        "SELECT n FROM c"
    )
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").side_effect = [
            chat_response(tool_calls=[tool_call("c1", "run_query", {"sql": sql})]),
            chat_response(content="查完了。"),
        ]
        await _collect(db_session, "查 100 筆")

    session_id = await _agent_session_id(db_session)
    history = await messages_repo.list_messages(db_session, session_id)
    tool_result_msg = next(
        m for m in history if '"type": "tool_result"' in m.content
    )
    observation = json.loads(json.loads(tool_result_msg.content)["observation"])
    assert len(observation["rows"]) == 20
    assert observation["truncated"] is True


# -- 純函式單元測試（transcript 編碼/重建、摘要）------------------------------


def test_decode_ai_content_plain_text_returns_none():
    assert agent_service._decode_ai_content("這是一般回覆文字") is None


def test_decode_ai_content_tool_call_roundtrip():
    encoded = agent_service._encode_tool_call("c1", "get_schema", {"db": "shop"})
    decoded = agent_service._decode_ai_content(encoded)
    assert decoded == {
        "type": "tool_call",
        "id": "c1",
        "tool": "get_schema",
        "args": {"db": "shop"},
    }


def test_summarize_variants():
    assert agent_service._summarize({"error": "壞了"}) == "錯誤：壞了"
    assert agent_service._summarize({"rows": [1, 2]}) == "2 筆結果"
    assert agent_service._summarize({"databases": ["a"]}) == "1 個資料庫"
    assert agent_service._summarize("not-a-dict") == "not-a-dict"
