"""`get_schema` 在大型資料庫下必須仍然可用（`docs/feature_backlog.md` 2-3）。

原本的行為：`_tool_get_schema` 回傳全部資料表的完整結構，`agent_service._obs_text`
再硬砍在 4,000 字元——截斷點是字元數，不是表的邊界。300 張表時使用者問
「orders 表有哪些欄位」，`orders` 很可能整段落在截斷點之後，模型拿到的是一份
在它出現前就被 `...(截斷)` 收尾的殘缺 JSON，於是憑殘缺資料硬猜（答錯），
或反覆重查（同參數必定得到同樣的截斷結果）。

專案裡本來就有現成解法沒被複用：`db_introspect.format_context()` 已寫好
「依表數量分級降階」的邏輯，但只有 Interviewer 在用。
"""

import pytest

from app.repos import settings as settings_repo
from app.repos.crypto import encrypt_db_url
from app.rules.spec_models import ColumnSpec, TableSpec
from app.services import tool_registry
from app.services.change_service import BUSINESS_DATABASES_KEY


def _table(name: str, column_count: int = 5) -> TableSpec:
    return TableSpec(
        table_name=name,
        description="",
        columns=[
            ColumnSpec(name=f"col_{i}", data_type="varchar", nullable=True, description="")
            for i in range(column_count)
        ],
    )


@pytest.fixture
def big_database(db_session, monkeypatch):
    """模擬一個有 300 張表的資料庫。"""
    tables = [_table(f"table_{i:03d}") for i in range(300)]
    tables.append(_table("orders", 8))  # 使用者真正要找的那張，排在最後

    async def fake_schema_tree(db_url):
        return tables, ""

    monkeypatch.setattr(tool_registry.dbops, "schema_tree", fake_schema_tree)

    async def _setup():
        await settings_repo.set_setting(
            db_session,
            BUSINESS_DATABASES_KEY,
            [{"name": "shop", "db_url_encrypted": encrypt_db_url("sqlite://")}],
        )
        await db_session.commit()

    return _setup, tables


async def test_specific_tables_can_be_requested(db_session, big_database):
    """模型應該能只要它需要的那幾張表，而不是每次都把整個資料庫拉回來。"""
    setup, _ = big_database
    await setup()
    ctx = tool_registry.ToolContext(db=db_session, db_name="shop")

    result = await tool_registry.dispatch("get_schema", {"tables": ["orders"]}, ctx)

    assert "error" not in result, result
    names = [t["table_name"] for t in result["tables"]]
    assert names == ["orders"]
    assert len(result["tables"][0]["columns"]) == 8, "指名要的表要給完整欄位"


async def test_unknown_table_name_says_so(db_session, big_database):
    setup, _ = big_database
    await setup()
    ctx = tool_registry.ToolContext(db=db_session, db_name="shop")

    result = await tool_registry.dispatch("get_schema", {"tables": ["no_such_table"]}, ctx)

    assert "error" in result or result.get("tables") == []


async def test_large_database_falls_back_to_a_summary(db_session, big_database):
    """沒有指名時不能把 300 張表的完整結構倒出來——那必定會被硬砍在字元邊界。"""
    setup, _ = big_database
    await setup()
    ctx = tool_registry.ToolContext(db=db_session, db_name="shop")

    result = await tool_registry.dispatch("get_schema", {}, ctx)

    assert "error" not in result, result
    assert result.get("truncated") is not True, "應改為摘要，而不是硬砍"
    assert result.get("summary"), "表很多時要回表名清單，不是完整結構"
    assert result.get("hint"), "要告訴模型怎麼拿到細節"
    assert "name_contains" in result["hint"], "表名清單可能被截，必須告訴模型可以用搜尋"
    assert str(result["table_count"]) in result["hint"], "要講清楚實際有幾張表"


async def test_a_table_outside_the_summary_is_still_findable(db_session, big_database):
    """表名清單受預算限制會被截，但模型仍要有辦法找到沒被列出來的表。

    這是「摘要涵蓋不了全部」的正確解法——與其硬塞，不如給搜尋。
    """
    setup, _ = big_database
    await setup()
    ctx = tool_registry.ToolContext(db=db_session, db_name="shop")

    listed = await tool_registry.dispatch("get_schema", {}, ctx)
    assert "orders" not in listed["summary"], "前置條件：orders 排在最後、不在清單裡"

    found = await tool_registry.dispatch("get_schema", {"name_contains": "order"}, ctx)
    names = [t["table_name"] for t in found["tables"]]
    assert "orders" in names
    assert len(found["tables"][0]["columns"]) == 8, "搜尋命中的表要給完整欄位"


async def test_summary_says_how_many_are_omitted(db_session, big_database):
    setup, _ = big_database
    await setup()
    ctx = tool_registry.ToolContext(db=db_session, db_name="shop")

    result = await tool_registry.dispatch("get_schema", {}, ctx)
    omitted = result["table_count"] - len(result["summary"])
    assert omitted > 0
    assert str(omitted) in result["hint"], "沒列出來的張數要講清楚，不能讓模型以為就這些"


async def test_small_database_still_returns_full_structure(db_session, monkeypatch):
    """表不多時維持原本行為——完整結構對模型最有用。"""
    tables = [_table("orders", 6), _table("refunds", 4)]

    async def fake_schema_tree(db_url):
        return tables, ""

    monkeypatch.setattr(tool_registry.dbops, "schema_tree", fake_schema_tree)
    await settings_repo.set_setting(
        db_session,
        BUSINESS_DATABASES_KEY,
        [{"name": "shop", "db_url_encrypted": encrypt_db_url("sqlite://")}],
    )
    await db_session.commit()

    ctx = tool_registry.ToolContext(db=db_session, db_name="shop")
    result = await tool_registry.dispatch("get_schema", {}, ctx)

    assert len(result["tables"]) == 2
    assert result["tables"][0]["columns"], "小型資料庫要給完整欄位"


async def test_summary_fits_inside_the_observation_budget(db_session, big_database):
    """摘要必須真的塞得進 observation 預算，否則等於沒解決問題。"""
    import json

    from app.services import agent_service

    setup, _ = big_database
    await setup()
    ctx = tool_registry.ToolContext(db=db_session, db_name="shop")

    result = await tool_registry.dispatch("get_schema", {}, ctx)
    text = agent_service._obs_text(result)

    assert "...(截斷)" not in text, "摘要仍被硬砍，代表分級降階做得不夠"
    assert len(json.dumps(result, ensure_ascii=False)) < agent_service.MAX_OBS_CHARS
