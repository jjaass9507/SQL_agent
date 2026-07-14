"""app/services/tool_registry.py 的單元測試。

策略：`get_schema`/`analyze_schema`/`check_conventions`/`find_related_tables`/
`check_table_docs` 都透過 `app.services.dbops.schema_tree` 取得既有結構——
`dbops.schema_tree` 本身（連同其呼叫的 `rules.db_introspect.extract_schema`）
已不在本階段檔案範圍內，這裡直接 monkeypatch `tool_registry.dbops.schema_tree`
拿到固定的 TableSpec 清單，只驗證 tool_registry 這層「參數解析 → 呼叫 rules →
格式化回傳」的轉接邏輯是否正確；`run_query`/`explain_query` 則直接對 SQLite
跑，驗證唯讀護欄真的擋下 DML（不需要 mock）。
"""

from app.rules.spec_models import ColumnSpec, TableSpec
from app.services import tool_registry
from tests.agent.conftest import install_fake_psycopg2, sample_table_dict, seed_business_db


def _ctx(db_session, db_name: str | None = None) -> tool_registry.ToolContext:
    return tool_registry.ToolContext(db=db_session, db_name=db_name)


def _existing_tables() -> list[TableSpec]:
    return [
        TableSpec(
            table_name="users",
            description="使用者",
            columns=[
                ColumnSpec("id", "integer", False, "PK", is_primary_key=True),
                ColumnSpec("name", "text", False, ""),
            ],
        )
    ]


# -- dispatch：未知工具 / 缺參數 / 例外一律回 {"error": ...} ------------------


async def test_dispatch_unknown_tool(db_session):
    result = await tool_registry.dispatch("no_such_tool", {}, _ctx(db_session))
    assert "error" in result


async def test_dispatch_args_not_dict(db_session):
    result = await tool_registry.dispatch("list_databases", "not-a-dict", _ctx(db_session))
    assert "error" in result


async def test_dispatch_missing_required_args(db_session):
    for name, args in [
        ("get_table_ddl", {}),
        ("run_query", {}),
        ("explain_query", {}),
        ("check_conventions", {}),
        ("find_related_tables", {}),
        ("draft_comment_ddl", {"table": "t"}),  # 缺 comments
        ("propose_ddl", {}),
    ]:
        result = await tool_registry.dispatch(name, args, _ctx(db_session))
        assert "error" in result, f"{name} 應回傳 error"


async def test_dispatch_handler_exception_becomes_error(db_session, monkeypatch):
    async def _boom(args, ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(tool_registry._REGISTRY["list_databases"], "handler", _boom)
    result = await tool_registry.dispatch("list_databases", {}, _ctx(db_session))
    assert "error" in result
    assert "boom" in result["error"]


# -- list_databases / 未設定業務資料庫 ----------------------------------------


async def test_list_databases_empty(db_session):
    result = await tool_registry.dispatch("list_databases", {}, _ctx(db_session))
    assert result == {"databases": []}


async def test_list_databases_returns_names(db_session):
    await seed_business_db(db_session, "shop", "sqlite://")
    result = await tool_registry.dispatch("list_databases", {}, _ctx(db_session))
    assert result == {"databases": ["shop"]}


async def test_get_schema_no_business_db_configured(db_session):
    result = await tool_registry.dispatch("get_schema", {}, _ctx(db_session))
    assert "error" in result
    assert "尚未設定" in result["error"]


async def test_get_schema_unknown_db_name(db_session):
    await seed_business_db(db_session, "shop", "sqlite://")
    result = await tool_registry.dispatch("get_schema", {"db": "other"}, _ctx(db_session))
    assert "error" in result


# -- get_schema / analyze_schema / check_conventions / find_related_tables ---
# -- check_table_docs：都透過 dbops.schema_tree（monkeypatch）---------------


async def test_get_schema_returns_tables(db_session, monkeypatch):
    await seed_business_db(db_session, "shop", "sqlite://")

    async def _fake_schema_tree(url):
        return _existing_tables(), ""

    monkeypatch.setattr(tool_registry.dbops, "schema_tree", _fake_schema_tree)
    result = await tool_registry.dispatch("get_schema", {"db": "shop"}, _ctx(db_session))
    assert "tables" in result
    assert result["tables"][0]["table_name"] == "users"


async def test_analyze_schema_returns_warnings(db_session, monkeypatch):
    await seed_business_db(db_session, "shop", "sqlite://")

    async def _fake_schema_tree(url):
        # 無主鍵的表 → schema_advisor 應回報警告
        return [TableSpec(table_name="no_pk", description="", columns=[
            ColumnSpec("x", "text", True, ""),
        ])], ""

    monkeypatch.setattr(tool_registry.dbops, "schema_tree", _fake_schema_tree)
    result = await tool_registry.dispatch("analyze_schema", {"db": "shop"}, _ctx(db_session))
    assert "warnings" in result
    assert len(result["warnings"]) >= 1


async def test_check_conventions_requires_design_tables(db_session):
    await seed_business_db(db_session, "shop", "sqlite://")
    result = await tool_registry.dispatch("check_conventions", {"db": "shop"}, _ctx(db_session))
    assert "error" in result


async def test_check_conventions_returns_conventions_and_warnings(db_session, monkeypatch):
    await seed_business_db(db_session, "shop", "sqlite://")

    async def _fake_schema_tree(url):
        return _existing_tables(), ""

    monkeypatch.setattr(tool_registry.dbops, "schema_tree", _fake_schema_tree)
    result = await tool_registry.dispatch(
        "check_conventions",
        {"db": "shop", "design_tables": [sample_table_dict("orders")]},
        _ctx(db_session),
    )
    assert "conventions" in result
    assert "warnings" in result


async def test_find_related_tables_requires_requirement(db_session):
    await seed_business_db(db_session, "shop", "sqlite://")
    result = await tool_registry.dispatch("find_related_tables", {"db": "shop"}, _ctx(db_session))
    assert "error" in result


async def test_find_related_tables_returns_related(db_session, monkeypatch):
    await seed_business_db(db_session, "shop", "sqlite://")

    async def _fake_schema_tree(url):
        return _existing_tables(), ""

    monkeypatch.setattr(tool_registry.dbops, "schema_tree", _fake_schema_tree)
    result = await tool_registry.dispatch(
        "find_related_tables",
        {"db": "shop", "requirement": "我要記錄 users 的訂單"},
        _ctx(db_session),
    )
    assert "related" in result
    assert "fk_suggestions" in result
    assert "duplicate_risks" in result


async def test_check_table_docs_returns_summary(db_session, monkeypatch):
    await seed_business_db(db_session, "shop", "sqlite://")

    async def _fake_schema_tree(url):
        return _existing_tables(), ""

    monkeypatch.setattr(tool_registry.dbops, "schema_tree", _fake_schema_tree)
    result = await tool_registry.dispatch("check_table_docs", {"db": "shop"}, _ctx(db_session))
    assert "summary" in result
    assert "missing" in result


# -- draft_comment_ddl（純規則，不需要連線）-----------------------------------


async def test_draft_comment_ddl(db_session):
    comments = {"table_comment": "使用者資料表", "columns": {"id": "主鍵"}}
    result = await tool_registry.dispatch(
        "draft_comment_ddl", {"table": "users", "comments": comments}, _ctx(db_session)
    )
    assert "ddl" in result
    assert "COMMENT ON TABLE" in result["ddl"]
    assert "COMMENT ON COLUMN" in result["ddl"]


# -- get_table_ddl（monkeypatch 私有同步 helper，避免依賴真實 PostgreSQL）----


async def test_get_table_ddl_requires_table(db_session):
    await seed_business_db(db_session, "shop", "sqlite://")
    result = await tool_registry.dispatch("get_table_ddl", {"db": "shop"}, _ctx(db_session))
    assert "error" in result


async def test_get_table_ddl_returns_ddl(db_session, monkeypatch):
    await seed_business_db(db_session, "shop", "sqlite://")

    def _fake_sync(url, table, schema):
        return {"ddl": f"CREATE TABLE {schema}.{table} (\n  id integer\n);"}

    monkeypatch.setattr(tool_registry, "_get_table_ddl_sync", _fake_sync)
    result = await tool_registry.dispatch(
        "get_table_ddl", {"db": "shop", "table": "users"}, _ctx(db_session)
    )
    assert result["ddl"].startswith("CREATE TABLE public.users")


# -- run_query / explain_query：真的對 SQLite 跑，驗證唯讀護欄 ----------------


async def test_run_query_rejects_dml(db_session):
    await seed_business_db(db_session, "shop", "sqlite://")
    result = await tool_registry.dispatch(
        "run_query", {"db": "shop", "sql": "DELETE FROM t"}, _ctx(db_session)
    )
    assert "error" in result


async def test_run_query_executes_select(db_session):
    await seed_business_db(db_session, "shop", "sqlite://")
    result = await tool_registry.dispatch(
        "run_query", {"db": "shop", "sql": "SELECT 1 AS a"}, _ctx(db_session)
    )
    assert result["columns"] == ["a"]
    assert result["rows"] == [[1]]


async def test_explain_query_works(db_session):
    await seed_business_db(db_session, "shop", "sqlite://")
    result = await tool_registry.dispatch(
        "explain_query", {"db": "shop", "sql": "SELECT 1"}, _ctx(db_session)
    )
    assert result["rows"]


# -- propose_ddl：委派 change_service（含 allowlist + dry-run）--------------


async def test_propose_ddl_requires_ddl(db_session):
    result = await tool_registry.dispatch("propose_ddl", {}, _ctx(db_session))
    assert "error" in result


async def test_propose_ddl_rejects_disallowed_ddl(db_session):
    await seed_business_db(db_session, "shop", "postgresql://x/y")
    result = await tool_registry.dispatch(
        "propose_ddl", {"db": "shop", "ddl": "DROP TABLE users;"}, _ctx(db_session)
    )
    assert "error" in result


async def test_propose_ddl_success_creates_pending_request(db_session, monkeypatch):
    install_fake_psycopg2(monkeypatch)
    await seed_business_db(db_session, "shop", "postgresql://x/y")
    result = await tool_registry.dispatch(
        "propose_ddl",
        {"db": "shop", "ddl": "CREATE INDEX idx_users_name ON users(name);", "reason": "加速查詢"},
        _ctx(db_session),
    )
    assert result["status"] == "pending"
    assert result["dry_run_ok"] is True
    assert "proposal_id" in result


async def test_tool_defs_are_openai_function_shaped():
    defs = tool_registry.tool_defs()
    assert len(defs) == 11
    for d in defs:
        assert d["type"] == "function"
        assert "name" in d["function"]
        assert "parameters" in d["function"]
