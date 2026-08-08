"""DB Agent 的工具目錄（native function calling 版）。

每個工具是既有 `app/services/dbops.py`／`app/rules/`／`app/services/change_service.py`
的薄轉接層（v0.5 `agents/tool_registry.py` 語意照舊，只是協定從 `<TOOL>` XML 標籤
改成 OpenAI 原生 function calling：`tool_defs()` 回傳的 JSON Schema 直接送進
`LLMProvider.chat(tools=...)`）。

`dispatch()` 對未知工具、缺參數、handler 內部例外一律回傳 `{"error": ...}`
而不 raise，讓錯誤能當作 observation 回饋給 LLM 自我修正。

`nl2sql` 不在此登錄——agent 自己寫 SQL 執行；`nl2sql` 是獨立的手動工作台功能。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repos import activity as activity_repo
from app.repos import settings as settings_repo
from app.rules import (
    convention_checker,
    metadata_checker,
    schema_advisor,
    sensitive_columns,
    spec_models,
    table_relation,
)
from app.services import change_service, dbops

logger = logging.getLogger(__name__)


# DB Agent 目前是全平台共用的一條對話，沒有具名使用者可歸屬。標記清楚比留白好：
# 空白看起來像資料遺失，明確標記才看得出「當時就是不知道是誰」。
ANONYMOUS_AGENT = "anonymous(db-agent)"


@dataclass
class ToolContext:
    """每回合共用的工具上下文。"""

    db: AsyncSession
    db_name: str | None = None  # 本回合選擇的資料庫（工具的 db 參數可覆蓋）
    actor: str | None = None  # 稽核用；AUTH_ENABLED=false 時為 None


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # OpenAI function calling 的 JSON Schema
    handler: Callable[[dict, ToolContext], Awaitable[dict]]


# ── 共用小工具 ────────────────────────────────────────────────────────────


def _require(args: dict, *names: str) -> str | None:
    missing = [n for n in names if not args.get(n)]
    if missing:
        return f"缺少必要參數：{', '.join(missing)}"
    return None


async def _resolve_db_url(ctx: ToolContext, args: dict) -> tuple[str | None, str | None]:
    """解析 args["db"]（或 ctx.db_name）對應的業務資料庫連線字串。"""
    name = args.get("db") or ctx.db_name
    _, url, err = await change_service.resolve_business_db(ctx.db, name)
    return url, err


def _get_table_ddl_sync(db_url: str, table: str, schema: str) -> dict:
    """從 information_schema 重建單一資料表的建表語句（僅供參考，非權威 DDL）。"""
    engine = create_engine(db_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT column_name, data_type, character_maximum_length, "
                    "is_nullable, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = :table "
                    "ORDER BY ordinal_position"
                ),
                {"schema": schema, "table": table},
            ).fetchall()
    finally:
        engine.dispose()

    if not rows:
        return {"error": f"資料表「{schema}.{table}」不存在或無欄位"}

    lines = []
    for cname, dtype, maxlen, nullable, default in rows:
        type_str = f"{dtype}({maxlen})" if maxlen else dtype
        null_str = "" if nullable == "YES" else " NOT NULL"
        def_str = f" DEFAULT {default}" if default else ""
        lines.append(f"  {cname} {type_str}{null_str}{def_str}")
    ddl = f"CREATE TABLE {schema}.{table} (\n" + ",\n".join(lines) + "\n);"
    return {"ddl": ddl}


_DB_PARAM = {"type": "string", "description": "資料庫名稱（可省略，預設為目前選擇的資料庫）"}
_DESIGN_TABLES_PARAM = {
    "type": "array",
    "description": (
        "正在設計的資料表，格式同 TableSpec：[{table_name, description, "
        "columns:[{name, data_type, nullable, description, is_primary_key, "
        "is_foreign_key, references, is_unique, is_indexed}]}]"
    ),
    "items": {"type": "object"},
}


# ── 工具 handler ─────────────────────────────────────────────────────────


async def _tool_list_databases(args: dict, ctx: ToolContext) -> dict:
    setting = await settings_repo.get_setting(ctx.db, change_service.BUSINESS_DATABASES_KEY)
    databases = setting.value_json if setting and setting.value_json else []
    return {"databases": [d.get("name") for d in databases]}


# 超過這個表數就不再回完整結構。observation 會被硬砍在 4,000 字元、而且是字元
# 邊界不是表邊界——使用者要的那張表可能整段落在截斷點之後，模型只好憑殘缺
# 資料硬猜，或用同樣的參數重查（截斷是決定性的，重查不會有新結果）。
_FULL_SCHEMA_MAX_TABLES = 15
_SUMMARY_MAX_NAMES = 150


async def _tool_get_schema(args: dict, ctx: ToolContext) -> dict:
    url, err = await _resolve_db_url(ctx, args)
    if err:
        return {"error": err}
    tables, err = await dbops.schema_tree(url)
    if err and not tables:
        return {"error": err}

    wanted = args.get("tables")
    if wanted:
        names = {str(n).lower() for n in wanted}
        picked = [t for t in tables if t.table_name.lower() in names]
        if not picked:
            available = ", ".join(t.table_name for t in tables[:20])
            return {"error": f"找不到指定的資料表。這個資料庫有：{available}…"}
        return {"tables": [spec_models.asdict(t) for t in picked]}

    keyword = (args.get("name_contains") or "").strip().lower()
    if keyword:
        matched = [t for t in tables if keyword in t.table_name.lower()]
        if not matched:
            return {"error": f"沒有資料表的名稱包含「{keyword}」。"}
        if len(matched) <= _FULL_SCHEMA_MAX_TABLES:
            return {"tables": [spec_models.asdict(t) for t in matched]}
        tables = matched

    if len(tables) > _FULL_SCHEMA_MAX_TABLES:
        # 只給表名，讓模型看得到「有哪些表」，再指名要細節。清單本身也可能超過
        # observation 預算，因此在**表名邊界**截斷並講清楚少了幾張——絕不能像
        # 原本那樣砍在字元中間，那會讓模型讀到半個表名而不自知。
        names = [t.table_name for t in tables]
        shown = names[:_SUMMARY_MAX_NAMES]
        omitted = len(names) - len(shown)
        hint = (
            f"這個資料庫有 {len(tables)} 張表，數量太多無法一次回傳完整結構。"
            '請用 tables 參數指名你需要的資料表（例如 {"tables": ["orders"]}）'
            '，或用 name_contains 依關鍵字搜尋（例如 {"name_contains": "order"}）。'
        )
        if omitted:
            hint += f"（表名清單只列出前 {len(shown)} 張，還有 {omitted} 張未列出）"
        return {"table_count": len(tables), "summary": shown, "hint": hint}

    return {"tables": [spec_models.asdict(t) for t in tables]}


async def _tool_get_table_ddl(args: dict, ctx: ToolContext) -> dict:
    err = _require(args, "table")
    if err:
        return {"error": err}
    url, err = await _resolve_db_url(ctx, args)
    if err:
        return {"error": err}
    schema = args.get("schema") or "public"
    try:
        return await asyncio.to_thread(_get_table_ddl_sync, url, args["table"], schema)
    except Exception as exc:
        return {"error": f"查詢失敗：{str(exc)[:200]}"}


async def _tool_run_query(args: dict, ctx: ToolContext) -> dict:
    err = _require(args, "sql")
    if err:
        return {"error": err}
    url, err = await _resolve_db_url(ctx, args)
    if err:
        return {"error": err}
    try:
        result = await dbops.execute_query(url, args["sql"])
    except dbops.QueryRejected as exc:
        return {"error": str(exc)}
    # agent 讀到的資料會進入全平台共用的 transcript 並送往 LLM——這裡遮，
    # 人工查詢頁不遮（那裡的使用者本來就有權限，且結果不進 LLM）。
    return sensitive_columns.mask_result(result)


async def _tool_explain_query(args: dict, ctx: ToolContext) -> dict:
    err = _require(args, "sql")
    if err:
        return {"error": err}
    url, err = await _resolve_db_url(ctx, args)
    if err:
        return {"error": err}
    try:
        return await dbops.explain_query(url, args["sql"])
    except dbops.QueryRejected as exc:
        return {"error": str(exc)}


async def _tool_analyze_schema(args: dict, ctx: ToolContext) -> dict:
    url, err = await _resolve_db_url(ctx, args)
    if err:
        return {"error": err}
    tables, err = await dbops.schema_tree(url)
    if err and not tables:
        return {"error": err}
    return {"warnings": schema_advisor.analyze(tables)}


async def _tool_check_conventions(args: dict, ctx: ToolContext) -> dict:
    design_tables_raw = args.get("design_tables")
    if not design_tables_raw:
        return {"error": "缺少必要參數：design_tables"}
    url, err = await _resolve_db_url(ctx, args)
    if err:
        return {"error": err}
    existing, err = await dbops.schema_tree(url)
    if err and not existing:
        return {"error": err}
    conventions = convention_checker.infer_conventions(existing)
    design_tables = spec_models.tables_from_json(design_tables_raw)
    warnings = convention_checker.check_conventions(design_tables, conventions)
    return {"conventions": conventions, "warnings": warnings}


async def _tool_find_related_tables(args: dict, ctx: ToolContext) -> dict:
    err = _require(args, "requirement")
    if err:
        return {"error": err}
    url, err = await _resolve_db_url(ctx, args)
    if err:
        return {"error": err}
    existing, err = await dbops.schema_tree(url)
    if err and not existing:
        return {"error": err}
    design_tables_raw = args.get("design_tables")
    design_tables = spec_models.tables_from_json(design_tables_raw) if design_tables_raw else None
    return table_relation.find_related(args["requirement"], design_tables, existing)


async def _tool_check_table_docs(args: dict, ctx: ToolContext) -> dict:
    url, err = await _resolve_db_url(ctx, args)
    if err:
        return {"error": err}
    existing, err = await dbops.schema_tree(url)
    if err and not existing:
        return {"error": err}
    return metadata_checker.check_metadata_completeness(existing)


async def _tool_draft_comment_ddl(args: dict, ctx: ToolContext) -> dict:
    err = _require(args, "table", "comments")
    if err:
        return {"error": err}
    db_name = args.get("db") or ctx.db_name
    ddl = metadata_checker.draft_comment_ddl(db_name, args["table"], args["comments"])
    return {"ddl": ddl}


async def _tool_propose_ddl(args: dict, ctx: ToolContext) -> dict:
    """Terminal 工具（見 agent_service）：allowlist → dry-run → 建立 pending 變更提案。
    永不自己執行 DDL。"""
    err = _require(args, "ddl")
    if err:
        return {"error": err}
    db_name = args.get("db") or ctx.db_name
    return await change_service.create_change_request(
        ctx.db, db_name, args["ddl"], args.get("reason", "")
    )


# ── registry ─────────────────────────────────────────────────────────────

_REGISTRY: dict[str, Tool] = {}


def _register(tool: Tool) -> None:
    _REGISTRY[tool.name] = tool


_register(Tool(
    name="list_databases",
    description="列出所有已設定的業務資料庫名稱。",
    parameters={"type": "object", "properties": {}},
    handler=_tool_list_databases,
))
_register(Tool(
    name="get_schema",
    description=(
        "取得資料庫結構（資料表、欄位、型態、PK/FK、註解）。"
        "資料表很多時只會回傳表名清單——請用 tables 參數指名你需要的資料表，"
        "才會拿到完整欄位。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "db": _DB_PARAM,
            "tables": {
                "type": "array",
                "description": "只取這幾張資料表的完整結構（省略則回傳全部或表名摘要）",
                "items": {"type": "string"},
            },
            "name_contains": {
                "type": "string",
                "description": "依關鍵字搜尋資料表名稱（資料表很多時用這個找到目標）",
            },
        },
    },
    handler=_tool_get_schema,
))
_register(Tool(
    name="get_table_ddl",
    description="從 information_schema 重建單一資料表的建表語句（僅供參考，非權威 DDL）。",
    parameters={
        "type": "object",
        "properties": {
            "db": _DB_PARAM,
            "table": {"type": "string", "description": "資料表名稱"},
            "schema": {"type": "string", "description": "PostgreSQL schema，預設 public"},
        },
        "required": ["table"],
    },
    handler=_tool_get_table_ddl,
))
_register(Tool(
    name="run_query",
    description="執行唯讀 SQL 查詢（僅限單一 SELECT/EXPLAIN 語句）並回傳欄位與資料列。",
    parameters={
        "type": "object",
        "properties": {
            "db": _DB_PARAM,
            "sql": {"type": "string", "description": "SELECT ... FROM ... LIMIT 100;"},
        },
        "required": ["sql"],
    },
    handler=_tool_run_query,
))
_register(Tool(
    name="explain_query",
    description="對一條 SELECT 查詢執行 EXPLAIN，取得執行計畫以分析效能問題。",
    parameters={
        "type": "object",
        "properties": {"db": _DB_PARAM, "sql": {"type": "string", "description": "SELECT ...;"}},
        "required": ["sql"],
    },
    handler=_tool_explain_query,
))
_register(Tool(
    name="analyze_schema",
    description=(
        "對指定資料庫的目前結構跑規則式設計檢查（缺 PK、外鍵無索引、明文密碼欄位等），零 API 成本。"
    ),
    parameters={"type": "object", "properties": {"db": _DB_PARAM}},
    handler=_tool_analyze_schema,
))
_register(Tool(
    name="check_conventions",
    description=(
        "檢查設計中的新資料表是否符合現有資料庫的建表慣例"
        "（命名風格、PK、時間欄位等，多數決推斷），零 API 成本。"
    ),
    parameters={
        "type": "object",
        "properties": {"db": _DB_PARAM, "design_tables": _DESIGN_TABLES_PARAM},
        "required": ["design_tables"],
    },
    handler=_tool_check_conventions,
))
_register(Tool(
    name="find_related_tables",
    description="分析需求文字（與可選的設計表）跟現有資料表的關聯：可重用的表、建議的外鍵、重複建表風險。",
    parameters={
        "type": "object",
        "properties": {
            "db": _DB_PARAM,
            "requirement": {"type": "string", "description": "需求文字"},
            "design_tables": _DESIGN_TABLES_PARAM,
        },
        "required": ["requirement"],
    },
    handler=_tool_find_related_tables,
))
_register(Tool(
    name="check_table_docs",
    description="檢查現有資料表的用途說明（table comment）與欄位說明（column comment）是否齊全。",
    parameters={"type": "object", "properties": {"db": _DB_PARAM}},
    handler=_tool_check_table_docs,
))
_register(Tool(
    name="draft_comment_ddl",
    description="把草擬好的資料表/欄位說明組成 COMMENT ON TABLE/COLUMN 語句，供 propose_ddl 提案。",
    parameters={
        "type": "object",
        "properties": {
            "db": _DB_PARAM,
            "table": {"type": "string", "description": "資料表名稱"},
            "comments": {
                "type": "object",
                "description": '{"table_comment": "...", "columns": {"欄位名": "說明"}}',
            },
        },
        "required": ["table", "comments"],
    },
    handler=_tool_draft_comment_ddl,
))
_register(Tool(
    name="propose_ddl",
    description=(
        "提案一項結構變更（CREATE TABLE、ALTER TABLE ADD COLUMN/CONSTRAINT、"
        "CREATE INDEX、COMMENT ON）。會先做 allowlist 檢查與 dry-run 驗證，"
        "通過後建立待審變更請求，交由管理員核准後才會實際執行。"
        "呼叫後本回合立即結束（terminal 工具）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "db": _DB_PARAM,
            "ddl": {"type": "string", "description": "CREATE INDEX ...;"},
            "reason": {"type": "string", "description": "為何需要此變更"},
        },
        "required": ["ddl"],
    },
    handler=_tool_propose_ddl,
))


def tool_defs() -> list[dict]:
    """回傳 OpenAI function calling 格式的工具目錄，直接送進 `LLMProvider.chat(tools=...)`。"""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in _REGISTRY.values()
    ]


# 會碰到業務資料庫、事後需要追溯的工具。純讀結構的工具（get_schema 等）不記，
# 否則稽核紀錄會被雜訊淹沒、真正該看的東西反而找不到。
_AUDITED_TOOLS = frozenset({"run_query", "explain_query", "propose_ddl", "draft_comment_ddl"})

_AUDIT_SQL_MAX = 500


async def _log_tool_call(name: str, args: dict, ctx: ToolContext, result: dict) -> None:
    """把 agent 對業務資料庫做的事寫進 activity_log。

    沒有這筆紀錄，「有沒有人透過 DB Agent 查過薪資表」事後完全查不出來——
    agent 能讀到真實資料，卻是全平台唯一沒有留痕的路徑。
    """
    if name not in _AUDITED_TOOLS:
        return
    statement = args.get("sql") or args.get("ddl") or ""
    try:
        await activity_repo.log_activity(
            ctx.db,
            "agent.tool_called",
            {
                "tool": name,
                "db": args.get("db") or ctx.db_name,
                "actor": ctx.actor or ANONYMOUS_AGENT,
                "sql": str(statement)[:_AUDIT_SQL_MAX],
                "ok": "error" not in result,
            },
        )
    except Exception:  # noqa: BLE001 - 稽核失敗不該讓使用者的操作跟著失敗
        logger.exception("audit log failed for tool %s", name)


async def dispatch(name: str, args: dict, ctx: ToolContext) -> dict:
    """查表並執行一個工具呼叫。永不 raise——錯誤一律回傳 {"error": ...}，
    讓 agent_service 能把它當作 observation 回饋給 LLM 自我修正。"""
    tool = _REGISTRY.get(name)
    if tool is None:
        return {"error": f"未知工具：{name}"}
    if not isinstance(args, dict):
        return {"error": "工具參數必須是 JSON 物件"}
    try:
        result = await tool.handler(args, ctx)
    except Exception as exc:
        logger.exception("tool %s failed", name)
        result = {"error": f"工具執行錯誤：{str(exc)[:200]}"}
    await _log_tool_call(name, args, ctx, result)
    return result
