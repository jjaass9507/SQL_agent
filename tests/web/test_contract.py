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
from tests.api.conftest import drive_to_confirming, interview_turn_payload, sample_table
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
    """建 session 並用 mock LLM 跑到 tables 就緒（提案 → 同意兩輪，phase=confirming）。"""
    session = (await client.post("/api/v1/sessions", json={})).json()
    await drive_to_confirming(
        client, session["id"], [sample_table("users")], summary=["需要使用者表"]
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

    tables = [sample_table("users")]
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").side_effect = [
            chat_completion_response(content=interview_turn_payload("我的規劃如下", tables=tables)),
            chat_completion_response(
                content=interview_turn_payload(
                    "好的，已整理出資料表。", tables=tables, summary=["摘要"], user_confirmed=True
                )
            ),
        ]
        await client.post(
            f"/api/v1/sessions/{session['id']}/messages", json={"content": "我要使用者表"}
        )
        resp = await client.post(
            f"/api/v1/sessions/{session['id']}/messages",
            json={"content": "可以"},
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


# ── 錯誤訊息覆蓋率（app/web/static/js/lib/api.js 的 STATUS_MESSAGE）────────────
#
# 後端拋出的每一個 4xx 都要有對應的中文說明，否則使用者只會看到通用句子。
# 5xx 由 api.js 的 `status >= 500` 分支統一兜底，不需逐碼列出。

_STATUS_CODE_RE = re.compile(r"status_code=(\d{3})")
_JS_STATUS_KEY_RE = re.compile(r"^\s*(\d{3}):", re.MULTILINE)


def _api_js_source() -> str:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    return (root / "app" / "web" / "static" / "js" / "lib" / "api.js").read_text(encoding="utf-8")


def test_every_backend_4xx_has_a_friendly_message():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    raised = set()
    for path in (root / "app" / "api").rglob("*.py"):
        raised.update(_STATUS_CODE_RE.findall(path.read_text(encoding="utf-8")))

    backend_4xx = {int(code) for code in raised if 400 <= int(code) < 500}
    covered = {int(code) for code in _JS_STATUS_KEY_RE.findall(_api_js_source())}

    missing = sorted(backend_4xx - covered)
    assert not missing, f"api.js 的 STATUS_MESSAGE 缺少這些狀態碼的中文說明：{missing}"


def test_api_js_does_not_dump_raw_detail_into_the_toast():
    """422 的 detail 是物件陣列，直接 JSON.stringify 會把整包丟到使用者臉上。"""
    source = _api_js_source()
    assert "friendlyMessage(response.status, detail)" in source
    assert "showToast(`操作失敗（${response.status}）${detail}`" not in source


def test_templates_load_no_external_assets():
    """正式機在無法連外的內網（docs/deployment.md），樣板不得依賴任何外部 CDN。

    以前 ER 圖從 cdn.jsdelivr.net 載 mermaid、字型從 fonts.googleapis.com 載，
    上線第一天圖就是壞的，且每頁都會有對外 timeout。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    offenders = []
    for path in (root / "app" / "web" / "templates").glob("*.html"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "http://" in line or "https://" in line:
                offenders.append(f"{path.name}:{lineno} {line.strip()}")
    assert not offenders, "樣板出現外部連線：\n" + "\n".join(offenders)


def test_mermaid_is_vendored_locally():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    vendored = root / "app" / "web" / "static" / "vendor" / "mermaid.min.js"
    assert vendored.is_file(), "缺少本地 mermaid，ER 圖在離線環境會渲染不出來"


# ── 文件渲染契約（app/web/static/js/lib/doc-render.js）────────────────────────
#
# 渲染器是自己寫的，只支援 # 標題 / | 表格 / - 與 1. 條列 / ``` 區塊 /
# **粗體** / `行內 code`。writer 若哪天開始輸出其他語法（引言、連結、圖片、
# 分隔線、巢狀清單），畫面會直接顯示原始符號而沒有任何錯誤——本測試先攔下來。

_UNSUPPORTED_MARKDOWN = {
    ">": "引言（blockquote）",
    "![": "圖片",
    "---": "分隔線",
    "===": "setext 標題",
}


def test_spec_writer_output_only_uses_supported_markdown():
    from app.rules.writers.spec_writer import SpecWriter
    from tests.specs import col, table

    tables = [
        table(
            "orders",
            "訂單主檔",
            [
                col("id", "uuid", False, "主鍵", is_primary_key=True),
                col("amount", "numeric", True, "金額", length=12),
            ],
            constraints=["CHECK (amount >= 0)"],
        )
    ]
    markdown = SpecWriter().generate(tables)

    offenders = []
    for lineno, line in enumerate(markdown.splitlines(), 1):
        stripped = line.strip()
        for marker, name in _UNSUPPORTED_MARKDOWN.items():
            if stripped.startswith(marker):
                offenders.append(f"{lineno}: {name} → {stripped[:40]}")
        # 巢狀清單（前導空白 + 條列符號）渲染器只當成同一層
        if line.startswith(("  -", "  *", "\t-")):
            offenders.append(f"{lineno}: 巢狀清單 → {stripped[:40]}")

    assert not offenders, (
        "spec_writer 產出了 doc-render.js 不支援的 Markdown 語法：\n" + "\n".join(offenders)
    )


def test_doc_render_never_uses_innerhtml():
    """渲染的是 LLM 產出的內容，必須全程 createElement + textContent。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (root / "app" / "web" / "static" / "js" / "lib" / "doc-render.js").read_text(
        encoding="utf-8"
    )
    assert "innerHTML" not in source
    assert "insertAdjacentHTML" not in source


# ── 破壞性操作的確認流程 ──────────────────────────────────────────────────
#
# 核准變更是全平台唯一不可逆的操作：app/services/change_service.py 會對正式
# 業務資料庫執行 DDL 並 commit。兩位受測使用者都把「怕誤按這顆」列為第一名。


def _js(*parts) -> str:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    return (root / "app" / "web" / "static" / "js" / Path(*parts)).read_text(encoding="utf-8")


def test_approve_change_request_is_gated_by_a_confirm_dialog():
    source = _js("pages", "agent.js")
    assert "confirmApprove" in source
    assert 'if (decision === "approve" && !(await confirmApprove(' in source, (
        "核准路徑必須先經過確認對話框"
    )


def test_confirm_dialog_requires_an_explicit_acknowledgement_for_approval():
    """使用者明說「按一個按鈕就過」不夠，要多一個動作才會停下來想一秒。"""
    source = _js("pages", "agent.js")
    assert "ackLabel:" in source, "核准對話框必須有必勾的確認框"


def test_removing_a_business_db_connection_is_confirmed():
    source = _js("pages", "settings.js")
    assert "confirmDialog" in source
    assert "資料庫本身" in source, "刪除連線的文案要講明不會影響資料庫本身"


def test_restore_version_has_no_dialog_because_it_is_not_destructive():
    """還原版本是把目標版本複製成新版本（session_service.restore_version），
    歷史完整保留，不該用對話框增加摩擦。這個測試防止之後有人「順手也加一個」。"""
    source = _js("pages", "confirm.js")
    assert "confirmDialog" not in source


def test_ddl_impact_warns_about_what_dry_run_cannot_catch():
    source = _js("lib", "ddl-impact.js")
    assert "CONCURRENTLY" in source, "大表建索引會鎖寫入，dry-run 在空表上測不到"
    assert "NOT\\s+NULL" in source or "NOT\\\\s+NULL" in source or "NOT" in source


def test_index_entry_buttons_explain_what_they_need_in_plain_language():
    """三個入口有兩個需要使用者先準備東西（連線字串／CREATE TABLE 語法），
    不說清楚的話不懂技術的人會選了才發現走不下去。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    html = (root / "app" / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    assert html.count("btn-entry-desc") == 3, "三個入口都要有一句白話說明"
    assert "向 IT 索取" in html
    assert "CREATE TABLE" in html


def test_chat_tells_the_user_why_the_confirm_button_is_disabled():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    html = (root / "app" / "web" / "templates" / "chat.html").read_text(encoding="utf-8")
    assert 'data-target="confirm-cta-hint"' in html


def test_extras_are_collapsed_and_labelled_as_optional():
    """八顆延伸產出按鈕平鋪會讓不懂技術的使用者「一個都不敢按」。
    不減量（各有真實使用者），但要收摺並標明選用，每顆講清楚用途。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    html = (root / "app" / "web" / "templates" / "docs.html").read_text(encoding="utf-8")
    assert "<details" in html, "延伸產出區要預設收摺"
    assert "選用" in html, "標題要標明選用，否則使用者以為是必要步驟"
    assert html.count("btn-entry-desc") == 8, "八顆按鈕都要有一句用途說明"
