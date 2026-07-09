"""Unit tests for agents/tool_registry.py — no real DB, no LLM."""
from agents.tool_registry import ToolContext, build_context, dispatch, render_catalog


def _ctx(url="postgresql://x/y", db_name=None):
    return ToolContext(resolve_db_url=lambda name: url, db_name=db_name)


# ── dispatch() error handling ────────────────────────────────────────────────

def test_dispatch_unknown_tool_returns_error_not_raise():
    result = dispatch("does_not_exist", {}, _ctx())
    assert "error" in result
    assert "does_not_exist" in result["error"]


def test_dispatch_non_dict_args_returns_error():
    result = dispatch("get_schema", "not a dict", _ctx())  # type: ignore
    assert "error" in result


def test_dispatch_missing_required_arg_returns_error():
    # get_table_ddl requires "table"
    result = dispatch("get_table_ddl", {}, _ctx())
    assert "error" in result
    assert "table" in result["error"]


def test_dispatch_run_query_missing_sql_returns_error():
    result = dispatch("run_query", {}, _ctx())
    assert "error" in result
    assert "sql" in result["error"]


# ── run_query rejects non-SELECT ─────────────────────────────────────────────

def test_run_query_rejects_non_select():
    result = dispatch("run_query", {"sql": "DELETE FROM users"}, _ctx())
    assert "error" in result


def test_run_query_rejects_stacked_statements():
    result = dispatch("run_query", {"sql": "SELECT 1; DROP TABLE x"}, _ctx())
    assert "error" in result


def test_run_query_allows_select(monkeypatch):
    import web.db_manager as db_manager
    monkeypatch.setattr(db_manager, "execute_query",
                        lambda url, sql, **kw: {"columns": ["id"], "rows": [[1]], "row_count": 1})
    result = dispatch("run_query", {"sql": "SELECT id FROM t"}, _ctx())
    assert result == {"columns": ["id"], "rows": [[1]], "row_count": 1}


def test_run_query_no_db_configured_returns_error():
    ctx = ToolContext(resolve_db_url=lambda name: None, db_name=None)
    result = dispatch("run_query", {"sql": "SELECT 1"}, ctx)
    assert "error" in result


# ── explain_query / get_schema / get_table_ddl delegate to db_manager ───────

def test_explain_query_rejects_non_select():
    result = dispatch("explain_query", {"sql": "INSERT INTO t VALUES (1)"}, _ctx())
    assert "error" in result


def test_get_schema_delegates_to_schema_tree(monkeypatch):
    import web.db_manager as db_manager
    monkeypatch.setattr(db_manager, "schema_tree", lambda url, schema: {"tables": [{"name": "orders"}]})
    result = dispatch("get_schema", {}, _ctx())
    assert result == {"tables": [{"name": "orders"}]}


def test_list_databases(monkeypatch):
    import web.app_settings as app_settings
    monkeypatch.setattr(app_settings, "get_business_databases",
                        lambda: [{"name": "訂單庫", "url": "x"}, {"name": "會員庫", "url": "y"}])
    result = dispatch("list_databases", {}, _ctx())
    assert result == {"databases": ["訂單庫", "會員庫"]}


def test_analyze_schema_returns_warnings(monkeypatch):
    import web.db_manager as db_manager
    monkeypatch.setattr(db_manager, "schema_tree", lambda url, schema: {"tables": [
        {"name": "users", "columns": [{"name": "id", "type": "integer", "nullable": False,
                                       "is_pk": False, "is_fk": False, "fk_table": None}]},
    ]})
    result = dispatch("analyze_schema", {}, _ctx())
    assert "warnings" in result
    # no PK on the only column-having table → should flag no_pk
    assert any(w.get("code") == "no_pk" for w in result["warnings"])


# ── tool handler exceptions are caught, not raised ───────────────────────────

def test_dispatch_catches_handler_exceptions(monkeypatch):
    import web.db_manager as db_manager

    def _boom(url, schema):
        raise RuntimeError("boom")

    monkeypatch.setattr(db_manager, "schema_tree", _boom)
    result = dispatch("get_schema", {}, _ctx())
    assert "error" in result


# ── build_context() resolution order ─────────────────────────────────────────

def test_build_context_prefers_explicit_db_arg(monkeypatch):
    import web.app_settings as app_settings
    monkeypatch.setattr(app_settings, "get_business_database",
                        lambda name: {"name": name, "url": f"url-for-{name}"} if name in ("a", "b") else None)
    monkeypatch.setattr(app_settings, "get_business_databases", lambda: [{"name": "a", "url": "url-for-a"}])
    ctx = build_context(db_name="a")
    assert ctx.resolve_db_url("b") == "url-for-b"
    assert ctx.resolve_db_url(None) == "url-for-a"


def test_build_context_no_databases_returns_none(monkeypatch):
    import web.app_settings as app_settings
    monkeypatch.setattr(app_settings, "get_business_databases", lambda: [])
    ctx = build_context(db_name=None)
    assert ctx.resolve_db_url(None) is None


# ── render_catalog() ──────────────────────────────────────────────────────────

def test_render_catalog_lists_all_registered_tools():
    catalog = render_catalog()
    for name in ("list_databases", "get_schema", "get_table_ddl", "run_query",
                 "explain_query", "analyze_schema"):
        assert name in catalog


def test_nl2sql_not_registered():
    result = dispatch("nl2sql", {}, _ctx())
    assert "error" in result
    assert "nl2sql" in result["error"]


# ── Phase 3 tools: check_conventions / find_related_tables / check_table_docs /
#    draft_comment_ddl ─────────────────────────────────────────────────────────

def test_render_catalog_lists_phase3_tools():
    catalog = render_catalog()
    for name in ("check_conventions", "find_related_tables", "check_table_docs", "draft_comment_ddl"):
        assert name in catalog


def test_check_conventions_requires_design_tables(monkeypatch):
    import web.db_introspect as db_introspect
    monkeypatch.setattr(db_introspect, "extract_schema", lambda url, schema="public": ([], ""))
    result = dispatch("check_conventions", {}, _ctx())
    assert "error" in result


def test_check_conventions_delegates(monkeypatch):
    import web.db_introspect as db_introspect
    from models.schema import ColumnSpec, TableSpec
    existing = [
        TableSpec(table_name=f"t{i}", description="", columns=[
            ColumnSpec(name="id", data_type="integer", nullable=False, description="", is_primary_key=True),
            ColumnSpec(name="created_at", data_type="timestamptz", nullable=False, description=""),
        ]) for i in range(3)
    ]
    monkeypatch.setattr(db_introspect, "extract_schema", lambda url, schema="public": (existing, ""))
    result = dispatch("check_conventions", {
        "design_tables": [{"table_name": "userProfile", "description": "", "columns": [
            {"name": "id", "data_type": "integer", "nullable": False, "description": "", "is_primary_key": True},
        ]}],
    }, _ctx())
    assert "warnings" in result
    assert any(w["code"] == "convention_naming" for w in result["warnings"])


def test_find_related_tables_requires_requirement():
    result = dispatch("find_related_tables", {}, _ctx())
    assert "error" in result


def test_find_related_tables_delegates(monkeypatch):
    import web.db_introspect as db_introspect
    from models.schema import ColumnSpec, TableSpec
    existing = [TableSpec(table_name="orders", description="訂單主檔", columns=[
        ColumnSpec(name="id", data_type="integer", nullable=False, description="", is_primary_key=True),
    ])]
    monkeypatch.setattr(db_introspect, "extract_schema", lambda url, schema="public": (existing, ""))
    result = dispatch("find_related_tables", {"requirement": "訂單查詢"}, _ctx())
    assert "related" in result and "fk_suggestions" in result and "duplicate_risks" in result


def test_check_table_docs_delegates(monkeypatch):
    import web.db_introspect as db_introspect
    from models.schema import ColumnSpec, TableSpec
    existing = [TableSpec(table_name="orders", description="", columns=[
        ColumnSpec(name="id", data_type="integer", nullable=False, description="")])]
    monkeypatch.setattr(db_introspect, "extract_schema", lambda url, schema="public": (existing, ""))
    result = dispatch("check_table_docs", {}, _ctx())
    assert "summary" in result


def test_draft_comment_ddl_requires_table_and_comments():
    result = dispatch("draft_comment_ddl", {}, _ctx())
    assert "error" in result


def test_draft_comment_ddl_builds_ddl():
    result = dispatch("draft_comment_ddl", {"table": "orders", "comments": {"table_comment": "訂單"}}, _ctx())
    assert "COMMENT ON TABLE" in result["ddl"]
