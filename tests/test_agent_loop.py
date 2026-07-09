"""Tests for agents/agent_loop.py — the ReAct tool-calling loop.

No real LLM, no real DB: the LLM is a scripted fake client, and any tool that
would touch a real database is monkeypatched at the web.db_manager /
web.app_settings level (same pattern as tests/test_tool_registry.py).
"""
import json

import pytest

import agents.agent_loop as agent_loop
from models.schema import ColumnSpec, TableSpec
from web.session_store import create_session, get_session


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_data(tmp_path, monkeypatch):
    import web.app_settings as settings
    import web.session_store as ss
    monkeypatch.setattr(ss, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "_SETTINGS_PATH", tmp_path / "app_settings.json")
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture(autouse=True)
def _one_business_db(monkeypatch):
    """A single configured business DB so _compact_schema_summary() and
    build_context() have something to resolve without hitting a real DB."""
    import web.app_settings as app_settings
    import web.db_manager as db_manager
    monkeypatch.setattr(app_settings, "get_business_databases",
                        lambda: [{"name": "demo", "url": "postgresql://x/demo"}])
    monkeypatch.setattr(app_settings, "get_business_database",
                        lambda name: {"name": "demo", "url": "postgresql://x/demo"} if name == "demo" else None)
    monkeypatch.setattr(db_manager, "schema_tree",
                        lambda url, schema: {"tables": [{"name": "orders", "columns": []}]})


class _FakeLLMClient:
    """Returns scripted responses, one per call to chat_messages(); records
    every call's messages for assertions."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[list[dict]] = []

    def chat_messages(self, messages, system_prompt=None):
        self.calls.append(messages)
        if not self._responses:
            return "<FINAL>（腳本用盡）</FINAL>"
        return self._responses.pop(0)


def _new_session():
    return create_session("測試對話", mode="agent")["id"]


def _flatten(messages: list[dict]) -> str:
    return "\n".join(
        p.get("text", "") for m in messages for p in m.get("content", []) if isinstance(p, dict)
    )


# ── (a) TOOL -> observation -> TOOL -> observation -> FINAL chain ───────────

def test_tool_chain_get_schema_then_run_query_then_final(monkeypatch):
    import web.db_manager as db_manager
    monkeypatch.setattr(
        db_manager, "execute_query",
        lambda url, sql, **kw: {
            "columns": ["id"],
            "rows": [[i] for i in range(25)],  # 25 rows > MAX_OBS_ROWS(20) to test truncation
            "row_count": 25,
            "truncated": False,
        },
    )

    fake = _FakeLLMClient([
        '<TOOL name="get_schema">{}</TOOL>',
        '<TOOL name="run_query">{"sql": "SELECT id FROM orders"}</TOOL>',
        '<FINAL>orders 資料表有 25 筆資料。</FINAL>',
    ])
    monkeypatch.setattr(agent_loop, "get_api", lambda: fake)

    sid = _new_session()
    result = agent_loop.run_agent_turn(sid, "orders 有多少筆資料？", db_name="demo")

    assert len(fake.calls) == 3
    assert result["reply"] == "orders 資料表有 25 筆資料。"
    assert len(result["steps"]) == 2
    assert result["steps"][0]["tool"] == "get_schema"
    assert result["steps"][1]["tool"] == "run_query"

    # Observation fed back to the LLM must be truncated to MAX_OBS_ROWS.
    second_call_text = _flatten(fake.calls[1])
    assert '<OBSERVATION tool="get_schema">' in second_call_text

    third_call_text = _flatten(fake.calls[2])
    assert '<OBSERVATION tool="run_query">' in third_call_text
    obs_match = third_call_text.split('<OBSERVATION tool="run_query">')[1].split("</OBSERVATION>")[0]
    obs_json = json.loads(obs_match)
    assert len(obs_json["rows"]) == agent_loop.MAX_OBS_ROWS
    assert obs_json["truncated"] is True

    # Persisted to session_store: user, tool x2, ai.
    session = get_session(sid)
    roles = [m["role"] for m in session["messages"]]
    assert roles == ["user", "tool", "tool", "ai"]


# ── (b) MAX_STEPS stop ────────────────────────────────────────────────────────

def test_stops_at_max_steps(monkeypatch):
    import web.db_manager as db_manager
    monkeypatch.setattr(db_manager, "execute_query",
                        lambda url, sql, **kw: {"columns": ["x"], "rows": [[1]], "row_count": 1})

    # 9 consecutive TOOL responses — one more than MAX_STEPS(8).
    responses = ['<TOOL name="run_query">{"sql": "SELECT 1"}</TOOL>' for _ in range(9)]
    fake = _FakeLLMClient(responses)
    monkeypatch.setattr(agent_loop, "get_api", lambda: fake)

    sid = _new_session()
    result = agent_loop.run_agent_turn(sid, "一直查詢", db_name="demo")

    assert len(fake.calls) == agent_loop.MAX_STEPS
    assert len(result["steps"]) == agent_loop.MAX_STEPS
    assert "已達到單回合最大工具呼叫次數" in result["reply"]


# ── (c) bad JSON args → error observation → self-correction ─────────────────

def test_bad_json_args_then_recovers(monkeypatch):
    import web.db_manager as db_manager
    monkeypatch.setattr(db_manager, "execute_query",
                        lambda url, sql, **kw: {"columns": ["x"], "rows": [[1]], "row_count": 1})

    fake = _FakeLLMClient([
        '<TOOL name="run_query">{not valid json</TOOL>',
        '<TOOL name="run_query">{"sql": "SELECT 1"}</TOOL>',
        '<FINAL>結果是 1。</FINAL>',
    ])
    monkeypatch.setattr(agent_loop, "get_api", lambda: fake)

    sid = _new_session()
    result = agent_loop.run_agent_turn(sid, "查一下", db_name="demo")

    assert len(fake.calls) == 3
    assert result["reply"] == "結果是 1。"
    assert len(result["steps"]) == 2
    assert "JSON" in result["steps"][0]["result_summary"]
    assert result["steps"][1]["tool"] == "run_query"

    # The observation fed back after the bad JSON must describe the parse error.
    second_call_text = _flatten(fake.calls[1])
    assert "JSON 解析失敗" in second_call_text


def test_two_consecutive_bad_json_aborts(monkeypatch):
    fake = _FakeLLMClient([
        '<TOOL name="run_query">{bad</TOOL>',
        '<TOOL name="run_query">{also bad</TOOL>',
        '<TOOL name="run_query">{"sql": "SELECT 1"}</TOOL>',  # should never be reached
    ])
    monkeypatch.setattr(agent_loop, "get_api", lambda: fake)

    sid = _new_session()
    result = agent_loop.run_agent_turn(sid, "查一下", db_name="demo")

    assert len(fake.calls) == 2  # aborts after MAX_JSON_FAILS consecutive failures
    assert "格式持續錯誤" in result["reply"]


# ── (d) restart simulation: transcript rebuilds across a fresh get_api() ────

def test_restart_rebuilds_transcript_for_next_turn(monkeypatch):
    sid = _new_session()

    fake1 = _FakeLLMClient(['<FINAL>第一輪回覆。</FINAL>'])
    monkeypatch.setattr(agent_loop, "get_api", lambda: fake1)
    result1 = agent_loop.run_agent_turn(sid, "第一輪問題", db_name="demo")
    assert result1["reply"] == "第一輪回覆。"

    # Simulate a process restart: a brand-new fake LLM client, no shared
    # in-memory state at all — only session_store ties the two turns together.
    fake2 = _FakeLLMClient(['<FINAL>第二輪回覆。</FINAL>'])
    monkeypatch.setattr(agent_loop, "get_api", lambda: fake2)
    result2 = agent_loop.run_agent_turn(sid, "第二輪問題", db_name="demo")
    assert result2["reply"] == "第二輪回覆。"

    assert len(fake2.calls) == 1
    rebuilt_text = _flatten(fake2.calls[0])
    assert "第一輪問題" in rebuilt_text
    assert "第一輪回覆。" in rebuilt_text
    assert "第二輪問題" in rebuilt_text


# ── DDL_SUGGESTION / DESIGN_REQUEST extraction from <FINAL> ──────────────────

def test_final_extracts_ddl_suggestion(monkeypatch):
    fake = _FakeLLMClient([
        '<FINAL>建議加欄位。\n<DDL_SUGGESTION db="demo">\nALTER TABLE orders ADD COLUMN note text;\n</DDL_SUGGESTION>\n</FINAL>',
    ])
    monkeypatch.setattr(agent_loop, "get_api", lambda: fake)
    sid = _new_session()
    result = agent_loop.run_agent_turn(sid, "幫我加個備註欄位", db_name="demo")

    assert result["ddl_suggestion"] == {"db": "demo", "sql": "ALTER TABLE orders ADD COLUMN note text;"}
    assert "DDL_SUGGESTION" not in result["reply"]
    assert "建議加欄位" in result["reply"]


def test_final_extracts_design_request(monkeypatch):
    fake = _FakeLLMClient([
        '<FINAL>好的，我來幫你設計。\n<DESIGN_REQUEST>使用者想要一個用戶系統</DESIGN_REQUEST>\n</FINAL>',
    ])
    monkeypatch.setattr(agent_loop, "get_api", lambda: fake)
    sid = _new_session()
    result = agent_loop.run_agent_turn(sid, "我想建一個用戶系統", db_name="demo")

    assert result["design_request"] == "使用者想要一個用戶系統"
    assert "DESIGN_REQUEST" not in result["reply"]


def test_no_tool_no_final_tag_treated_as_reply(monkeypatch):
    """If the LLM forgets the <FINAL> wrapper but also doesn't call a tool,
    the whole response is still treated as the final reply (per protocol:
    'no tag' also ends the loop)."""
    fake = _FakeLLMClient(['外鍵是用來維護關聯完整性的欄位。'])
    monkeypatch.setattr(agent_loop, "get_api", lambda: fake)
    sid = _new_session()
    result = agent_loop.run_agent_turn(sid, "什麼是外鍵？", db_name="demo")

    assert result["reply"] == "外鍵是用來維護關聯完整性的欄位。"
    assert len(fake.calls) == 1


# ── Phase 3: convention check / relation analysis / doc-completeness chains ──

def test_design_flow_calls_find_related_then_check_conventions(monkeypatch):
    import web.db_introspect as db_introspect
    existing = [TableSpec(table_name="products", description="商品主檔", columns=[
        ColumnSpec(name="id", data_type="integer", nullable=False, description="", is_primary_key=True),
        ColumnSpec(name="name", data_type="varchar", nullable=True, description=""),
    ])]
    monkeypatch.setattr(db_introspect, "extract_schema", lambda url, schema="public": (existing, ""))

    fake = _FakeLLMClient([
        '<TOOL name="find_related_tables">{"requirement": "新增訂單項目"}</TOOL>',
        ('<TOOL name="check_conventions">{"design_tables": [{"table_name": "order_items", '
         '"description": "", "columns": [{"name": "id", "data_type": "integer", "nullable": false, '
         '"description": "", "is_primary_key": true}]}]}</TOOL>'),
        '<FINAL>已完成分析。</FINAL>',
    ])
    monkeypatch.setattr(agent_loop, "get_api", lambda: fake)

    sid = _new_session()
    result = agent_loop.run_agent_turn(sid, "我想新增訂單項目表", db_name="demo")

    assert len(fake.calls) == 3
    assert result["steps"][0]["tool"] == "find_related_tables"
    assert "error" not in result["steps"][0]["result"]
    assert result["steps"][1]["tool"] == "check_conventions"
    assert "error" not in result["steps"][1]["result"]
    assert result["reply"] == "已完成分析。"


def test_check_docs_flow_calls_check_table_docs_then_draft_comment_ddl(monkeypatch):
    import web.db_introspect as db_introspect
    existing = [TableSpec(table_name="orders", description="", columns=[
        ColumnSpec(name="id", data_type="integer", nullable=False, description=""),
    ])]
    monkeypatch.setattr(db_introspect, "extract_schema", lambda url, schema="public": (existing, ""))

    fake = _FakeLLMClient([
        '<TOOL name="check_table_docs">{}</TOOL>',
        '<TOOL name="draft_comment_ddl">{"table": "orders", "comments": {"table_comment": "訂單主檔"}}</TOOL>',
        '<FINAL>已草擬說明。</FINAL>',
    ])
    monkeypatch.setattr(agent_loop, "get_api", lambda: fake)

    sid = _new_session()
    result = agent_loop.run_agent_turn(sid, "檢查文件完整性", db_name="demo")

    assert len(fake.calls) == 3
    assert result["steps"][0]["tool"] == "check_table_docs"
    assert "error" not in result["steps"][0]["result"]
    assert result["steps"][1]["tool"] == "draft_comment_ddl"
    assert "error" not in result["steps"][1]["result"]
    assert "COMMENT ON TABLE" in result["steps"][1]["result"]["ddl"]
    assert result["reply"] == "已草擬說明。"


# ── Phase 4: propose_ddl is a terminal tool ──────────────────────────────────

def _isolate_change_requests(monkeypatch, tmp_path):
    import web.change_requests as change_requests
    monkeypatch.setattr(change_requests, "DATA_DIR", tmp_path)
    monkeypatch.setattr(change_requests, "_JSON_PATH", tmp_path / "change_requests.json")
    return change_requests


def test_propose_ddl_ends_loop_without_further_llm_call(monkeypatch, tmp_path):
    change_requests = _isolate_change_requests(monkeypatch, tmp_path)
    import web.ddl_validator as ddl_validator
    monkeypatch.setattr(ddl_validator, "validate_ddl", lambda ddl, url: {"ok": True})

    fake = _FakeLLMClient([
        '<TOOL name="propose_ddl">{"ddl": "CREATE INDEX idx_x ON orders(created_at);", "reason": "慢查詢"}</TOOL>',
        '<FINAL>（不應該被呼叫到這裡）</FINAL>',
    ])
    monkeypatch.setattr(agent_loop, "get_api", lambda: fake)

    sid = _new_session()
    result = agent_loop.run_agent_turn(sid, "幫我加個索引", db_name="demo")

    # Terminal: only the one LLM call that emitted the <TOOL propose_ddl> — the
    # loop must not call the LLM again for a <FINAL> wrap-up.
    assert len(fake.calls) == 1
    assert result["steps"][0]["tool"] == "propose_ddl"
    assert result["proposal"] is not None
    assert result["proposal"]["proposal_id"]
    assert result["proposal"]["status"] == "pending"
    assert "已提交結構變更提案" in result["reply"]
    assert result["proposal"]["proposal_id"] in result["reply"]

    stored = change_requests.get(result["proposal"]["proposal_id"])
    assert stored["status"] == "pending"

    # Persisted transcript: user, tool (propose_ddl call+observation), ai.
    session = get_session(sid)
    assert [m["role"] for m in session["messages"]] == ["user", "tool", "ai"]


def test_propose_ddl_dry_run_failure_still_terminal(monkeypatch, tmp_path):
    change_requests = _isolate_change_requests(monkeypatch, tmp_path)
    import web.ddl_validator as ddl_validator
    monkeypatch.setattr(ddl_validator, "validate_ddl", lambda ddl, url: {"ok": False, "error": "語法錯誤"})

    fake = _FakeLLMClient([
        '<TOOL name="propose_ddl">{"ddl": "CREATE INDEX idx_x ON orders(created_at);"}</TOOL>',
        '<FINAL>（不應該被呼叫到這裡）</FINAL>',
    ])
    monkeypatch.setattr(agent_loop, "get_api", lambda: fake)

    sid = _new_session()
    result = agent_loop.run_agent_turn(sid, "幫我加個索引", db_name="demo")

    assert len(fake.calls) == 1  # still terminal even when the tool errors
    assert result["proposal"] is None
    assert "提案未成立" in result["reply"]
    assert change_requests.list_requests() == []


# ── unknown conversation id ───────────────────────────────────────────────────

def test_unknown_conversation_id_returns_graceful_error(monkeypatch):
    fake = _FakeLLMClient([])
    monkeypatch.setattr(agent_loop, "get_api", lambda: fake)
    result = agent_loop.run_agent_turn("does-not-exist", "hi", db_name="demo")
    assert result["steps"] == []
    assert "找不到" in result["reply"]
    assert len(fake.calls) == 0
