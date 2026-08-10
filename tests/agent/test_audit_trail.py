"""稽核留痕：誰、對哪個資料庫、做了什麼，事後必須查得出來。

這組測試的由來（`docs/feature_backlog.md` 0-3）：`changes.py` 的 approve/reject
端點透過 `require_admin_role` 依賴已經取得身分，卻沒有把它傳給 service，導致
「上週那個 ALTER TABLE 是誰核准的」永遠答不出來。DB Agent 的 `run_query` 工具
則完全沒有寫入 activity_log——「有沒有人透過 DB Agent 查過薪資表」同樣查不到。

與其他缺口不同的是：這一項不是「缺少某個能力」，而是**資訊正在永久流失**。
補得越晚，能追溯的歷史越短。
"""

import uuid

import pytest

from app.repos import activity as activity_repo
from app.repos import change_requests as cr_repo
from app.repos import settings as settings_repo
from app.repos.crypto import encrypt_db_url
from app.services import change_service, tool_registry
from app.services.change_service import BUSINESS_DATABASES_KEY


@pytest.fixture
def register_db(db_session):
    async def _register(name="shop", url="sqlite://"):
        await settings_repo.set_setting(
            db_session,
            BUSINESS_DATABASES_KEY,
            [{"name": name, "db_url_encrypted": encrypt_db_url(url)}],
        )
        await db_session.commit()

    return _register


async def _events(db_session, event: str) -> list[dict]:
    entries = await activity_repo.list_activity(db_session)
    return [e.detail_json or {} for e in entries if e.event == event]


# ── 誰核准了 DDL ────────────────────────────────────────────────────────


async def test_approval_records_who_decided(db_session, register_db):
    await register_db()
    record = await cr_repo.create_change_request(
        db_session, db_name="shop", ddl="CREATE TABLE t (id int);", reason="測試"
    )
    await db_session.commit()

    await change_service.approve_change_request(
        db_session, record.id, actor="alice@example.com"
    )

    # 執行成功或失敗都是決策事件，兩種都必須記得下按鈕的人是誰
    # （這個測試環境是 SQLite，dry-run 會走失敗路徑）。
    decisions = await _events(db_session, "change_request.executed") + await _events(
        db_session, "change_request.failed"
    )
    assert decisions, "核准後應寫入 activity_log"
    assert decisions[0].get("actor") == "alice@example.com", (
        "核准者身分必須入帳，否則事後追不出是誰按的"
    )


async def test_rejection_records_who_decided(db_session, register_db):
    await register_db()
    record = await cr_repo.create_change_request(
        db_session, db_name="shop", ddl="CREATE TABLE t (id int);", reason=""
    )
    await db_session.commit()

    await change_service.reject_change_request(db_session, record.id, actor="bob@example.com")

    logged = await _events(db_session, "change_request.rejected")
    assert logged and logged[0].get("actor") == "bob@example.com"


async def test_actor_falls_back_to_a_meaningful_marker(db_session, register_db):
    """AUTH_ENABLED=false 時沒有具名使用者，但仍要記下「是誰的權限做的」。

    留白會讓稽核紀錄看起來像資料遺失；明確標成 admin-token 才看得出當時
    是用共用權杖操作的。
    """
    await register_db()
    record = await cr_repo.create_change_request(
        db_session, db_name="shop", ddl="CREATE TABLE t (id int);", reason=""
    )
    await db_session.commit()

    await change_service.reject_change_request(db_session, record.id, actor=None)

    logged = await _events(db_session, "change_request.rejected")
    assert logged[0].get("actor"), "actor 不可留空——空白看起來像資料遺失"


# ── DB Agent 查了什麼 ───────────────────────────────────────────────────


async def test_agent_run_query_is_logged_with_sql(db_session, register_db):
    """「有沒有人透過 DB Agent 查過薪資表」必須答得出來。"""
    await register_db()
    ctx = tool_registry.ToolContext(db=db_session, db_name="shop")

    await tool_registry.dispatch("run_query", {"sql": "SELECT 1 AS a"}, ctx)

    logged = await _events(db_session, "agent.tool_called")
    assert logged, "DB Agent 執行查詢後必須留下紀錄"
    assert logged[0].get("tool") == "run_query"
    assert "SELECT 1" in (logged[0].get("sql") or ""), "要記下查了什麼，不能只記筆數"


async def test_agent_ddl_proposal_is_logged(db_session, register_db):
    await register_db()
    ctx = tool_registry.ToolContext(
        db=db_session, db_name="shop", allow_schema_changes=True
    )

    await tool_registry.dispatch(
        "propose_ddl", {"ddl": "CREATE TABLE t (id int);", "reason": "測試"}, ctx
    )

    logged = await _events(db_session, "agent.tool_called")
    assert any(e.get("tool") == "propose_ddl" for e in logged)


async def test_logged_sql_is_truncated(db_session, register_db):
    """稽核紀錄不該被單一筆巨大的查詢塞爆。"""
    await register_db()
    ctx = tool_registry.ToolContext(db=db_session, db_name="shop")
    long_sql = "SELECT " + ", ".join(f"'{i}' AS c{i}" for i in range(500))

    await tool_registry.dispatch("run_query", {"sql": long_sql}, ctx)

    logged = await _events(db_session, "agent.tool_called")
    assert len(logged[0]["sql"]) <= 520


# ── 端點層：身分真的有往下傳 ────────────────────────────────────────────


async def test_router_passes_identity_down_to_the_service(
    client, session_factory, monkeypatch
):
    """這正是原本壞掉的地方：router 拿到身分卻沒有傳給 service。

    注意用的是 `session_factory` 而不是 `db_session`——路由層與服務層測試在
    這個 conftest 裡是兩個獨立的 in-memory DB。
    """
    from app.config import get_settings

    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    get_settings.cache_clear()

    async with session_factory() as db:
        await settings_repo.set_setting(
            db,
            BUSINESS_DATABASES_KEY,
            [{"name": "shop", "db_url_encrypted": encrypt_db_url("sqlite://")}],
        )
        record = await cr_repo.create_change_request(
            db, db_name="shop", ddl="CREATE TABLE t (id int);", reason=""
        )
        record_id = record.id
        await db.commit()

    resp = await client.post(
        f"/api/v1/change-requests/{record_id}/reject",
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert resp.status_code == 200, resp.text

    async with session_factory() as db:
        logged = await _events(db, "change_request.rejected")
    assert logged and logged[0].get("actor"), "經由端點駁回時 actor 仍是空的"


async def test_unknown_change_request_is_not_logged_as_a_decision(db_session):
    await change_service.reject_change_request(db_session, uuid.uuid4(), actor="alice")
    assert not await _events(db_session, "change_request.rejected")
