"""Agent 迴圈的三個缺口（`docs/feature_backlog.md` 0-2 / 2-2 / 2-5）。

1. 查詢結果未遮罩就進入共用 transcript 並送往 LLM
   DB Agent 是全平台共用的一條對話，未遮罩的薪資／個資會成為下一個人的上下文。
2. `propose_ddl` 失敗仍終止本回合
   dry-run 撞到真實資料的 UNIQUE 違例時，模型明明可以先查出哪些值重複再改法，
   卻被強制結束，使用者只拿到一句原始 Postgres 錯誤。
3. 重複呼叫同一工具同參數沒有偵測
   截斷是決定性的，重查必定得到相同結果，白白燒掉 8 次預算。
"""

import pytest

from app.repos import settings as settings_repo
from app.repos.crypto import encrypt_db_url
from app.services import tool_registry
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


# ── 1. 敏感欄位遮罩 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "column",
    ["salary", "password", "id_number", "ssn", "credit_card", "身分證字號", "薪資"],
)
async def test_sensitive_columns_are_masked_in_agent_results(db_session, register_db, column):
    """agent 讀到的資料會進入共用 transcript 並送往 LLM gateway。"""
    await register_db()
    ctx = tool_registry.ToolContext(db=db_session, db_name="shop")

    result = await tool_registry.dispatch(
        "run_query", {"sql": f"SELECT 12345 AS \"{column}\", 'ok' AS note"}, ctx
    )

    assert "error" not in result, result
    masked = dict(zip(result["columns"], result["rows"][0], strict=True))
    assert masked[column] == "***", f"「{column}」是敏感欄位，不該原樣送進 LLM"
    assert masked["note"] == "ok", "非敏感欄位不該被動到"


async def test_masking_is_case_insensitive(db_session, register_db):
    await register_db()
    ctx = tool_registry.ToolContext(db=db_session, db_name="shop")
    result = await tool_registry.dispatch("run_query", {"sql": 'SELECT 1 AS "Salary"'}, ctx)
    assert result["rows"][0][0] == "***"


async def test_ordinary_columns_are_untouched(db_session, register_db):
    await register_db()
    ctx = tool_registry.ToolContext(db=db_session, db_name="shop")
    result = await tool_registry.dispatch(
        "run_query", {"sql": "SELECT 42 AS amount, 'x' AS channel"}, ctx
    )
    assert result["rows"][0] == [42, "x"]


async def test_workbench_results_are_not_masked(db_session, register_db):
    """人工查詢頁的使用者本來就有權限看真實資料，遮了反而沒用。

    差別在於：agent 的結果會被送進 LLM 並留在共用 transcript，workbench 不會。
    """
    from app.services import workbench_service

    await register_db()
    result = await workbench_service.run_query_on_business_db(
        db_session, "shop", 'SELECT 12345 AS "salary"'
    )
    assert result["rows"][0][0] == 12345


# ── 3. 重複呼叫偵測 ─────────────────────────────────────────────────────


def test_repeated_identical_call_is_detected():
    from app.services import agent_service

    seen = {}
    key = agent_service.tool_call_key("get_schema", {"db": "shop"})
    assert key not in seen
    seen[key] = "結果"
    assert agent_service.tool_call_key("get_schema", {"db": "shop"}) in seen


def test_argument_order_does_not_change_the_key():
    from app.services import agent_service

    a = agent_service.tool_call_key("run_query", {"sql": "SELECT 1", "db": "shop"})
    b = agent_service.tool_call_key("run_query", {"db": "shop", "sql": "SELECT 1"})
    assert a == b


def test_different_arguments_are_different_calls():
    from app.services import agent_service

    a = agent_service.tool_call_key("run_query", {"sql": "SELECT 1"})
    b = agent_service.tool_call_key("run_query", {"sql": "SELECT 2"})
    assert a != b


# ── 4. 查詢併發上限 ─────────────────────────────────────────────────────


async def test_concurrent_queries_are_capped(monkeypatch):
    """每次查詢都新開連線，沒有上限的話多人同時操作會頂滿正式庫的 max_connections。

    30 秒的 statement_timeout 對「短查詢但很多人同時打」完全無效。

    這裡刻意把上限壓到 2：預設的 10 大於 asyncio 執行緒池能同時跑的量，
    量到的會是執行緒池而不是這道閘門——第一版就是這樣寫的，把上限改成 999
    測試依然通過，等於什麼都沒驗到。
    """
    import asyncio
    import time

    from app.services import dbops

    monkeypatch.setattr(dbops, "_query_slots", asyncio.Semaphore(2))

    peak = 0
    running = 0

    def fake_run_sync(db_url, sql, max_rows):
        nonlocal peak, running
        running += 1
        peak = max(peak, running)
        time.sleep(0.02)
        running -= 1
        return {"columns": [], "rows": [], "truncated": False}

    monkeypatch.setattr(dbops, "_run_sync", fake_run_sync)
    await asyncio.gather(*(dbops.execute_query("sqlite://", "SELECT 1") for _ in range(12)))

    assert peak <= 2, f"同時有 {peak} 個查詢在跑，閘門沒有生效"


async def test_explain_shares_the_same_concurrency_gate(monkeypatch):
    """EXPLAIN 也會開連線，不能繞過閘門。"""
    import asyncio
    import time

    from app.services import dbops

    monkeypatch.setattr(dbops, "_query_slots", asyncio.Semaphore(2))
    peak = 0
    running = 0

    def fake_run_sync(db_url, sql, max_rows):
        nonlocal peak, running
        running += 1
        peak = max(peak, running)
        time.sleep(0.02)
        running -= 1
        return {"columns": [], "rows": [], "truncated": False}

    monkeypatch.setattr(dbops, "_run_sync", fake_run_sync)
    await asyncio.gather(*(dbops.explain_query("sqlite://", "SELECT 1") for _ in range(12)))
    assert peak <= 2
