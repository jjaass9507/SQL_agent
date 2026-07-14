"""前端頁面依賴的 API 契約測試（respx mock LLM + SQLite，走真實 FastAPI app）。

各頁 JS（app/web/static/js/pages/*.js）沒有 JS 測試框架，改在此固定它們依賴的
請求/回應形狀：欄位名稱、SSE 事件序列與 payload key。任何後端改動破壞這些形狀
（前端會壞掉）時，本檔測試會先失敗。
"""

import json
import re
import uuid

import respx

from app.api.routers import outputs as outputs_router
from app.repos import jobs as jobs_repo
from app.repos import sessions as sessions_repo
from app.workers.runner import poll_once
from tests.api.conftest import interview_turn_payload, sample_table
from tests.llm.conftest import chat_completion_response
from tests.web.conftest import BASE_URL
from tests.workers.conftest import dispatch_by_marker

_EVENT_RE = re.compile(r"event: (\w+)\ndata: (.+)\n\n")

# 四份核心文件產出時三個 LLM writer 的 system prompt 分辨標記（同 tests/workers）。
_WRITER_MARKERS = {
    "PostgreSQL DDL 腳本": "CREATE TABLE users (id uuid PRIMARY KEY);",
    "關聯設計決策": "使用者資料表獨立管理帳號資訊。",
    "效能與安全規劃書": "## 索引策略\n建議為 email 建索引。",
}


async def _create_confirming_session(client) -> dict:
    """建 session 並用 mock LLM 跑到 tables 就緒（phase=confirming）。"""
    session = (await client.post("/api/v1/sessions", json={})).json()
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(
                content=interview_turn_payload(
                    "設計完成", tables=[sample_table("users")], summary=["需要使用者表"]
                )
            )
        )
        await client.post(
            f"/api/v1/sessions/{session['id']}/messages",
            json={"content": "我要一張使用者表"},
        )
    return session


# ── index.js：session 列表欄位 ───────────────────────────────────────────


async def test_session_list_has_fields_index_page_depends_on(client):
    await client.post("/api/v1/sessions", json={"title": "第一筆"})

    resp = await client.get("/api/v1/sessions")

    assert resp.status_code == 200
    entry = resp.json()[0]
    # index.js 依賴：id（導頁）、title、mode（標籤）、phase（篩選/pill）、created_at（顯示時間）
    assert set(entry) >= {"id", "title", "mode", "phase", "created_at"}
    assert entry["phase"] == "collecting"


# ── chat.js：SSE delta/turn_done 事件 payload key ───────────────────────


async def test_message_sse_payload_keys_chat_page_depends_on(client):
    session = (await client.post("/api/v1/sessions", json={})).json()

    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(
                content=interview_turn_payload(
                    "好的，已整理出資料表。", tables=[sample_table("users")], summary=["摘要"]
                )
            )
        )
        resp = await client.post(
            f"/api/v1/sessions/{session['id']}/messages",
            json={"content": "我要使用者表"},
            headers={"Accept": "text/event-stream"},
        )

    events = _EVENT_RE.findall(resp.text)
    names = [name for name, _ in events]
    assert names[-1] == "turn_done"
    assert all(name == "delta" for name in names[:-1])

    # chat.js 依賴 delta payload key 為 "delta"
    first_delta = json.loads(events[0][1])
    assert "delta" in first_delta

    # chat.js 依賴 turn_done 的 reply / tables_ready / tables / summary；
    # 進度面板依賴 tables[].table_name 與 tables[].columns
    turn_done = json.loads(events[-1][1])
    assert set(turn_done) >= {"reply", "tables_ready", "tables", "summary"}
    assert turn_done["tables_ready"] is True
    table = turn_done["tables"][0]
    assert "table_name" in table
    assert isinstance(table["columns"], list)


# ── confirm.js：session 詳情 + 版本列表/還原 ─────────────────────────────


async def test_session_detail_and_versions_confirm_page_depends_on(client):
    session = await _create_confirming_session(client)

    detail = (await client.get(f"/api/v1/sessions/{session['id']}")).json()
    # confirm.js 依賴：latest_tables（表格渲染）、latest_key_points（摘要）、
    # context_tables（差異比對）、phase
    assert set(detail) >= {"latest_tables", "latest_key_points", "context_tables", "phase", "jobs"}
    assert detail["phase"] == "confirming"
    assert detail["latest_tables"][0]["table_name"] == "users"
    column = detail["latest_tables"][0]["columns"][0]
    assert set(column) >= {"name", "data_type", "nullable", "description", "is_primary_key"}

    versions = (await client.get(f"/api/v1/sessions/{session['id']}/versions")).json()
    assert len(versions) == 1
    assert set(versions[0]) >= {"version_num", "tables", "key_points", "created_at"}

    restored = await client.post(
        f"/api/v1/sessions/{session['id']}/versions/{versions[0]['version_num']}/restore"
    )
    assert restored.status_code == 200
    assert restored.json()["version_num"] == versions[0]["version_num"] + 1


# ── docs.js：confirm → 生成 job → outputs 全流程 ────────────────────────


async def test_design_flow_confirm_generate_outputs_docs_page_depends_on(
    client, session_factory
):
    session = await _create_confirming_session(client)

    confirm = (await client.post(f"/api/v1/sessions/{session['id']}/confirm")).json()
    # docs.js 依賴 confirm 回應的 job_id 與 phase
    assert set(confirm) >= {"session_id", "phase", "job_id"}
    assert confirm["phase"] == "generating"

    detail = (await client.get(f"/api/v1/sessions/{session['id']}")).json()
    job = next(j for j in detail["jobs"] if j["kind"] == "generate")
    # docs.js 依賴 job 的 id/kind/status/progress_json
    assert set(job) >= {"id", "kind", "status", "progress_json"}
    assert job["status"] == "queued"

    # 跑背景 worker 一輪（mock 三個 LLM writer）
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(side_effect=dispatch_by_marker(_WRITER_MARKERS))
        processed = await poll_once(session_factory)
    assert processed == 1

    detail = (await client.get(f"/api/v1/sessions/{session['id']}")).json()
    job = next(j for j in detail["jobs"] if j["kind"] == "generate")
    assert job["status"] == "done"
    # docs.js 進度卡依賴 progress_json 的 key 為固定檔名、值為狀態字串
    assert job["progress_json"] == {
        "01_specification.md": "done",
        "02_er_diagram.md": "done",
        "03_ddl.sql": "done",
        "04_security_plan.md": "done",
    }

    outputs = (await client.get(f"/api/v1/sessions/{session['id']}/outputs")).json()
    # docs.js 依賴 outputs[].filename / content / created_at
    assert {o["filename"] for o in outputs} == set(job["progress_json"])
    for output in outputs:
        assert set(output) >= {"filename", "content", "created_at"}
        assert isinstance(output["content"], str)

    zip_resp = await client.get(f"/api/v1/sessions/{session['id']}/outputs/zip")
    assert zip_resp.status_code == 200
    assert zip_resp.headers["content-type"] == "application/zip"


async def test_extras_generate_creates_job_then_output_appears(client, session_factory):
    session = await _create_confirming_session(client)

    resp = await client.post(f"/api/v1/sessions/{session['id']}/extras/dbml/generate")
    assert resp.status_code == 200
    body = resp.json()
    # docs.js 依賴 job_id（接 events SSE）與 status
    assert set(body) >= {"job_id", "status"}
    assert body["status"] == "queued"

    # dbml 是純模板 writer，不需要 LLM
    await poll_once(session_factory)

    outputs = (await client.get(f"/api/v1/sessions/{session['id']}/outputs")).json()
    assert any(o["filename"] == "schema.dbml" for o in outputs)


async def test_events_sse_generation_status_payload_keys(client, session_factory, monkeypatch):
    # SSE 端點內部用全域 get_session_factory，改指向測試的 session_factory
    monkeypatch.setattr(outputs_router, "get_session_factory", lambda: session_factory)

    async with session_factory() as db:
        session = await sessions_repo.create_session(db)
        job = await jobs_repo.create_job(db, session.id, kind="generate", payload_json={})
        await jobs_repo.update_job_progress(db, job.id, {"01_specification.md": "done"})
        await jobs_repo.finish_job(db, job.id, status="done")
        await db.commit()
        session_id, job_id = session.id, job.id

    resp = await client.get(f"/api/v1/sessions/{session_id}/events?job_id={job_id}")
    assert resp.status_code == 200
    events = _EVENT_RE.findall(resp.text)
    assert events and events[0][0] == "generation_status"
    payload = json.loads(events[0][1])
    # docs.js / review.js 依賴 job_id / status / progress / error
    assert set(payload) >= {"job_id", "kind", "status", "progress", "error"}
    assert payload["status"] == "done"


# ── agent.js / lib/agent-chat.js：agent chat SSE 事件 payload key ────────


async def test_agent_chat_sse_payload_keys(client):
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(content="這是資料庫助手的回覆。")
        )
        resp = await client.post(
            "/api/v1/agent/chat",
            json={"message": "你好"},
            headers={"Accept": "text/event-stream"},
        )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _EVENT_RE.findall(resp.text)
    names = [name for name, _ in events]
    assert names[-1] == "turn_done"

    # lib/agent-chat.js 依賴 delta payload key 為 "text"（與 sessions SSE 的 "delta" 不同）
    delta = json.loads(next(data for name, data in events if name == "delta"))
    assert "text" in delta

    turn_done = json.loads(events[-1][1])
    assert set(turn_done) >= {"reply", "steps", "proposal", "design_request"}


# ── agent.js：change-requests 列表/審批權限 ─────────────────────────────


async def test_change_requests_list_and_admin_guard(client):
    resp = await client.get("/api/v1/change-requests?status=pending")
    assert resp.status_code == 200
    assert resp.json() == []

    # 未設定 ADMIN_TOKEN 時審批回 403（agent.js 以 toast 呈現錯誤）
    resp = await client.post(f"/api/v1/change-requests/{uuid.uuid4()}/approve")
    assert resp.status_code == 403


# ── settings.js：settings / activity / llm health 形狀 ──────────────────


async def test_settings_and_activity_payload_keys(client):
    settings = (await client.get("/api/v1/settings")).json()
    # settings.js 依賴 backend / masked_url / business_databases[].name+masked_url
    assert set(settings) >= {"configured", "backend", "masked_url", "business_databases"}
    assert isinstance(settings["business_databases"], list)

    activity = (await client.get("/api/v1/activity?limit=10")).json()
    assert isinstance(activity, list)


async def test_llm_health_payload_keys(client):
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(content="pong")
        )
        resp = await client.get("/api/v1/llm/health")

    assert resp.status_code == 200
    body = resp.json()
    # settings.js 依賴 ok / model / profile 的五個能力欄位
    assert set(body) >= {"ok", "model", "profile"}
    assert set(body["profile"]) >= {
        "multi_turn",
        "system_role",
        "native_tools",
        "json_schema",
        "streaming",
    }


# ── index.js：DDL 匯入 ──────────────────────────────────────────────────


async def test_ddl_import_payload_keys(client):
    resp = await client.post(
        "/api/v1/ddl-import",
        json={"title": "匯入", "ddl": "CREATE TABLE users (id uuid PRIMARY KEY);"},
    )
    assert resp.status_code == 201
    body = resp.json()
    # index.js 依賴 id（導向 /confirm/{id}）與 table_count（toast 訊息）
    assert set(body) >= {"id", "table_count"}
    assert body["table_count"] == 1

    detail = (await client.get(f"/api/v1/sessions/{body['id']}")).json()
    assert detail["phase"] == "confirming"
    assert detail["latest_tables"][0]["table_name"] == "users"
