"""Tool registry for the ReAct-style DB agent loop.

Each tool is a thin adapter (~5 lines) over an existing `web/` module. Tools
are looked up by name and dispatched with a JSON args dict; unknown tools or
missing required args return {"error": ...} instead of raising, so the agent
loop can feed the error back to the LLM as an observation and let it retry.

`nl2sql` is intentionally NOT registered here — the agent writes its own SQL
via `run_query`; nl2sql stays a separate, manual workbench feature.
"""
import logging
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolContext:
    """Per-turn context passed to every tool handler."""
    resolve_db_url: Callable[[Optional[str]], Optional[str]]
    db_name: Optional[str] = None


@dataclass
class Tool:
    name: str
    description: str
    args_doc: str  # short JSON example shown to the LLM in the tool catalog
    handler: Callable[[dict, ToolContext], dict]
    read_only: bool = True


def build_context(db_name: str | None) -> ToolContext:
    """Build a ToolContext whose resolve_db_url() honours an explicit tool
    arg (args["db"]) first, then falls back to the turn's selected db_name,
    then the first configured business database."""

    def _resolve(name: str | None) -> str | None:
        from web.app_settings import get_business_database, get_business_databases
        target = name or db_name
        if target and target != "__all__":
            db = get_business_database(target)
            return db["url"] if db else None
        dbs = get_business_databases()
        return dbs[0]["url"] if dbs else None

    return ToolContext(resolve_db_url=_resolve, db_name=db_name)


# ── helpers ──────────────────────────────────────────────────────────────────

def _require(args: dict, *names: str) -> str | None:
    missing = [n for n in names if not args.get(n)]
    if missing:
        return f"缺少必要參數：{', '.join(missing)}"
    return None


def _resolve(args: dict, ctx: ToolContext) -> tuple[str | None, str | None]:
    """Resolve the target DB URL from args['db'] or ctx.db_name. Returns (url, error)."""
    name = args.get("db") or ctx.db_name
    url = ctx.resolve_db_url(name)
    if not url:
        return None, f"找不到資料庫：{name or '(未指定，且尚未設定任何業務資料庫)'}"
    return url, None


def _schema_tree_to_tablespecs(tables: list[dict]) -> list:
    from models.schema import ColumnSpec, TableSpec
    result = []
    for t in tables:
        cols = [
            ColumnSpec(
                name=c.get("name", ""),
                data_type=c.get("type", ""),
                nullable=c.get("nullable", True),
                description="",
                is_primary_key=c.get("is_pk", False),
                is_foreign_key=c.get("is_fk", False),
                references=c.get("fk_table"),
            )
            for c in t.get("columns", [])
        ]
        result.append(TableSpec(table_name=t.get("name", ""), description="", columns=cols))
    return result


# ── tool handlers ────────────────────────────────────────────────────────────

def _tool_list_databases(args: dict, ctx: ToolContext) -> dict:
    from web.app_settings import get_business_databases
    return {"databases": [d["name"] for d in get_business_databases()]}


def _tool_get_schema(args: dict, ctx: ToolContext) -> dict:
    from web.db_manager import schema_tree
    url, err = _resolve(args, ctx)
    if err:
        return {"error": err}
    return schema_tree(url, None)


def _tool_get_table_ddl(args: dict, ctx: ToolContext) -> dict:
    err = _require(args, "table")
    if err:
        return {"error": err}
    from web.db_manager import get_table_ddl
    url, err = _resolve(args, ctx)
    if err:
        return {"error": err}
    return get_table_ddl(url, args["table"], args.get("schema") or "public")


def _tool_run_query(args: dict, ctx: ToolContext) -> dict:
    err = _require(args, "sql")
    if err:
        return {"error": err}
    from web.sql_safety import check_read_only
    safety_err = check_read_only(args["sql"])
    if safety_err:
        return {"error": safety_err}
    from web.db_manager import execute_query
    url, err = _resolve(args, ctx)
    if err:
        return {"error": err}
    return execute_query(url, args["sql"])


def _tool_explain_query(args: dict, ctx: ToolContext) -> dict:
    err = _require(args, "sql")
    if err:
        return {"error": err}
    from web.db_manager import explain_query
    url, err = _resolve(args, ctx)
    if err:
        return {"error": err}
    return explain_query(url, args["sql"])


def _tool_analyze_schema(args: dict, ctx: ToolContext) -> dict:
    from web.db_manager import schema_tree
    from web.schema_advisor import analyze
    url, err = _resolve(args, ctx)
    if err:
        return {"error": err}
    tree = schema_tree(url, None)
    if "error" in tree:
        return tree
    warnings = analyze(_schema_tree_to_tablespecs(tree.get("tables", [])))
    return {"warnings": warnings}


def _tool_check_conventions(args: dict, ctx: ToolContext) -> dict:
    design_tables = args.get("design_tables")
    if not design_tables:
        return {"error": "缺少必要參數：design_tables"}
    from web.convention_checker import check_conventions, infer_conventions
    from web.db_introspect import extract_schema
    from web.session_store import tables_from_json
    url, err = _resolve(args, ctx)
    if err:
        return {"error": err}
    existing, ex_err = extract_schema(url)
    if ex_err and not existing:
        return {"error": ex_err}
    conventions = infer_conventions(existing)
    warnings = check_conventions(tables_from_json(design_tables), conventions)
    return {"conventions": conventions, "warnings": warnings}


def _tool_find_related_tables(args: dict, ctx: ToolContext) -> dict:
    err = _require(args, "requirement")
    if err:
        return {"error": err}
    from web.db_introspect import extract_schema
    from web.session_store import tables_from_json
    from web.table_relation import find_related
    url, err = _resolve(args, ctx)
    if err:
        return {"error": err}
    existing, ex_err = extract_schema(url)
    if ex_err and not existing:
        return {"error": ex_err}
    design_tables = tables_from_json(args.get("design_tables")) or None
    return find_related(args["requirement"], design_tables, existing)


def _tool_check_table_docs(args: dict, ctx: ToolContext) -> dict:
    from web.db_introspect import extract_schema
    from web.metadata_checker import check_metadata_completeness
    url, err = _resolve(args, ctx)
    if err:
        return {"error": err}
    existing, ex_err = extract_schema(url)
    if ex_err and not existing:
        return {"error": ex_err}
    return check_metadata_completeness(existing)


def _tool_draft_comment_ddl(args: dict, ctx: ToolContext) -> dict:
    err = _require(args, "table", "comments")
    if err:
        return {"error": err}
    from web.metadata_checker import draft_comment_ddl
    db_name = args.get("db") or ctx.db_name
    ddl = draft_comment_ddl(db_name, args["table"], args["comments"])
    return {"ddl": ddl}


# ── registry ─────────────────────────────────────────────────────────────────

_REGISTRY: dict[str, Tool] = {}


def _register(tool: Tool) -> None:
    _REGISTRY[tool.name] = tool


_register(Tool(
    name="list_databases",
    description="列出所有已設定的業務資料庫名稱。",
    args_doc='{}',
    handler=_tool_list_databases,
))
_register(Tool(
    name="get_schema",
    description="取得指定資料庫的完整結構（所有 schema 的資料表、欄位、型態、PK/FK）。",
    args_doc='{"db": "資料庫名稱（可省略，預設為目前選擇的資料庫）"}',
    handler=_tool_get_schema,
))
_register(Tool(
    name="get_table_ddl",
    description="從 information_schema 重建單一資料表的建表語句（僅供參考，非權威 DDL）。",
    args_doc='{"db": "資料庫名稱（可省略）", "table": "資料表名稱", "schema": "PostgreSQL schema，預設 public"}',
    handler=_tool_get_table_ddl,
))
_register(Tool(
    name="run_query",
    description="執行唯讀 SQL 查詢（僅限單一 SELECT/EXPLAIN 語句）並回傳欄位與資料列。",
    args_doc='{"db": "資料庫名稱（可省略）", "sql": "SELECT ... FROM ... LIMIT 100;"}',
    handler=_tool_run_query,
))
_register(Tool(
    name="explain_query",
    description="對一條 SELECT 查詢執行 EXPLAIN，取得執行計畫以分析效能問題。",
    args_doc='{"db": "資料庫名稱（可省略）", "sql": "SELECT ...;"}',
    handler=_tool_explain_query,
))
_register(Tool(
    name="analyze_schema",
    description="對指定資料庫的目前結構跑規則式設計檢查（缺 PK、外鍵無索引、明文密碼欄位等），零 API 成本。",
    args_doc='{"db": "資料庫名稱（可省略）"}',
    handler=_tool_analyze_schema,
))
_register(Tool(
    name="check_conventions",
    description="檢查設計中的新資料表是否符合現有資料庫的建表慣例（命名風格、PK、時間欄位等，多數決推斷），零 API 成本。",
    args_doc=('{"db": "資料庫名稱（可省略）", "design_tables": '
              '[{"table_name": "...", "columns": [{"name": "...", "data_type": "...", '
              '"is_primary_key": true}]}]}'),
    handler=_tool_check_conventions,
))
_register(Tool(
    name="find_related_tables",
    description="分析需求文字（與可選的設計表）跟現有資料表的關聯：可重用的表、建議的外鍵、重複建表風險。",
    args_doc=('{"db": "資料庫名稱（可省略）", "requirement": "需求文字", '
              '"design_tables": "可省略，格式同 check_conventions"}'),
    handler=_tool_find_related_tables,
))
_register(Tool(
    name="check_table_docs",
    description="檢查現有資料表的用途說明（table comment）與欄位說明（column comment）是否齊全。",
    args_doc='{"db": "資料庫名稱（可省略）"}',
    handler=_tool_check_table_docs,
))
_register(Tool(
    name="draft_comment_ddl",
    description="把草擬好的資料表/欄位說明組成 COMMENT ON TABLE/COLUMN 語句，供以 DDL_SUGGESTION 呈現待確認。",
    args_doc=('{"db": "資料庫名稱（可省略）", "table": "資料表名稱", '
              '"comments": {"table_comment": "...", "columns": {"欄位名": "說明"}}}'),
    handler=_tool_draft_comment_ddl,
))


def render_catalog() -> str:
    """Render the tool list as a prompt section (name, purpose, example args)."""
    parts = []
    for tool in _REGISTRY.values():
        parts.append(f"- `{tool.name}`：{tool.description}\n  參數範例：`{tool.args_doc}`")
    return "\n".join(parts)


def dispatch(name: str, args: dict, ctx: ToolContext) -> dict:
    """Look up and run a tool. Never raises — errors come back as {"error": ...}
    so the agent loop can feed them to the LLM as an observation."""
    tool = _REGISTRY.get(name)
    if tool is None:
        return {"error": f"未知工具：{name}"}
    if not isinstance(args, dict):
        return {"error": "工具參數必須是 JSON 物件"}
    try:
        return tool.handler(args, ctx)
    except Exception as exc:
        logger.exception("tool %s failed", name)
        return {"error": f"工具執行錯誤：{str(exc)[:200]}"}
