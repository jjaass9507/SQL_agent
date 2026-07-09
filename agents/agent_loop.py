"""ReAct-style agent loop: LLM <-> tool calls, persisted per-turn via session_store.

Replaces the old single-shot agents/db_agent.py. Each call to run_agent_turn()
rebuilds the full transcript from session_store (stateless — safe across
restarts and multiple workers), runs a bounded tool-calling loop against the
LLM, and persists every step (user message, each tool call/observation, final
reply) back to the session so the next turn (even in a different process)
can pick up where this one left off.
"""
import json
import logging
import re
from pathlib import Path

from agents import tool_registry
from utils.client import get_api
from web import session_store

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = (Path(__file__).parent.parent / "prompts" / "agent_loop.txt").read_text(encoding="utf-8")

MAX_STEPS = 8
MAX_OBS_ROWS = 20
MAX_OBS_CHARS = 4_000
MAX_MESSAGES_CHARS = 24_000
MAX_JSON_FAILS = 2

_TOOL_RE = re.compile(r'<TOOL\s+name="([^"]+)">(.*?)</TOOL>', re.DOTALL)
_FINAL_RE = re.compile(r'<FINAL>(.*?)</FINAL>', re.DOTALL)
_DDL_TAG_RE = re.compile(r'<DDL_SUGGESTION(?:\s+db="([^"]*)")?>(.*?)</DDL_SUGGESTION>', re.DOTALL)
_DESIGN_TAG_RE = re.compile(r'<DESIGN_REQUEST>(.*?)</DESIGN_REQUEST>', re.DOTALL)


def _text_message(role: str, text: str) -> dict:
    return {"role": role, "content": [{"type": "text", "text": text}]}


def _compact_schema_summary(db_name: str | None) -> str:
    """List database + table names only (no columns) — kept small since the
    agent can call get_schema / get_table_ddl on demand for details."""
    from web.app_settings import get_business_database, get_business_databases
    from web.db_manager import schema_tree

    def _table_names(url: str) -> list[str]:
        result = schema_tree(url, None)
        if "error" in result:
            return []
        return [t.get("name", "") for t in result.get("tables", [])]

    if db_name and db_name != "__all__":
        db = get_business_database(db_name)
        if not db:
            return "（找不到指定資料庫）"
        names = _table_names(db["url"])
        return f"資料庫「{db_name}」的資料表：{', '.join(names) if names else '（無資料表）'}"

    dbs = get_business_databases()
    if not dbs:
        return "（尚未設定業務資料庫）"
    lines = []
    for db in dbs:
        names = _table_names(db["url"])
        lines.append(f"- {db['name']}：{', '.join(names) if names else '（無資料表）'}")
    return "\n".join(lines)


def _cap_rows(result: dict) -> dict:
    """Cap result rows at MAX_OBS_ROWS (each observation: max 20 rows)."""
    if isinstance(result, dict) and isinstance(result.get("rows"), list) and len(result["rows"]) > MAX_OBS_ROWS:
        result = dict(result)
        result["rows"] = result["rows"][:MAX_OBS_ROWS]
        result["truncated"] = True
    return result


def _obs_text(result: dict) -> str:
    """Serialise an (already row-capped) result and truncate the JSON text
    itself at MAX_OBS_CHARS (each observation: max 4,000 chars)."""
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > MAX_OBS_CHARS:
        text = text[:MAX_OBS_CHARS] + "...(截斷)"
    return text


def _summarize(result: dict) -> str:
    """One-line human summary of a tool result, for the 'steps' trail returned
    to the caller (used by the frontend to render a collapsible step trace)."""
    if not isinstance(result, dict):
        return str(result)[:200]
    if "error" in result:
        return f"錯誤：{result['error']}"
    if "rows" in result:
        return f"{len(result.get('rows', []))} 筆結果"
    if "tables" in result:
        return f"{len(result.get('tables', []))} 張資料表"
    if "warnings" in result:
        return f"{len(result.get('warnings', []))} 項警告"
    if "plan" in result:
        return "已取得執行計畫"
    if "ddl" in result:
        return "已取得建表語句"
    if "databases" in result:
        return f"{len(result.get('databases', []))} 個資料庫"
    return "完成"


def _build_history_messages(session_messages: list[dict]) -> list[dict]:
    """Rebuild the LLM message list from persisted session_store messages.

    user/ai map to user/assistant messages directly; a "tool" message (stored
    as JSON {tool, args, observation}) expands back into the assistant
    <TOOL> call it was and the user <OBSERVATION> that followed it, so a
    freshly-built transcript (e.g. after a restart) is indistinguishable from
    the live one the LLM originally saw.
    """
    messages: list[dict] = []
    for m in session_messages:
        role = m.get("role")
        content = m.get("content", "")
        if role == "user":
            messages.append(_text_message("user", content))
        elif role == "ai":
            messages.append(_text_message("assistant", content))
        elif role == "tool":
            try:
                data = json.loads(content)
            except (TypeError, ValueError):
                continue
            tool_name = data.get("tool", "")
            args = data.get("args")
            observation = data.get("observation", "")
            args_text = json.dumps(args, ensure_ascii=False) if args is not None else "{}"
            messages.append(_text_message("assistant", f'<TOOL name="{tool_name}">{args_text}</TOOL>'))
            messages.append(_text_message("user", f'<OBSERVATION tool="{tool_name}">{observation}</OBSERVATION>'))
    return messages


def _msg_chars(m: dict) -> int:
    return sum(len(p.get("text", "")) for p in m.get("content", []) if isinstance(p, dict))


def _trim_to_budget(messages: list[dict]) -> list[dict]:
    """Drop oldest messages until the total character budget is respected."""
    trimmed = list(messages)
    total = sum(_msg_chars(m) for m in trimmed)
    while total > MAX_MESSAGES_CHARS and len(trimmed) > 1:
        removed = trimmed.pop(0)
        total -= _msg_chars(removed)
    return trimmed


def run_agent_turn(conversation_id: str, user_message: str, db_name: str | None = None) -> dict:
    """Run one user turn through the ReAct tool-calling loop.

    Returns {"reply": str, "steps": [{"tool", "args", "result_summary", "result"}...],
    "ddl_suggestion": {"db", "sql"} | None, "design_request": str | None}.
    """
    session = session_store.get_session(conversation_id)
    if session is None:
        return {"reply": "找不到對話 session，請重新整理頁面。", "steps": [],
                "ddl_suggestion": None, "design_request": None}

    session_store.add_message(conversation_id, "user", user_message)

    history = _build_history_messages(session.get("messages", []))
    messages = history + [_text_message("user", user_message)]

    ctx = tool_registry.build_context(db_name)
    system_prompt = (
        _PROMPT_TEMPLATE
        .replace("{TOOL_CATALOG}", tool_registry.render_catalog())
        .replace("{SCHEMA_SUMMARY}", _compact_schema_summary(db_name))
    )

    api = get_api()
    steps: list[dict] = []
    json_fail_count = 0

    for _ in range(MAX_STEPS):
        messages = _trim_to_budget(messages)
        response = api.chat_messages(messages, system_prompt=system_prompt)

        if not response:
            reply = "抱歉，無法取得回應，請稍後再試。"
            session_store.add_message(conversation_id, "ai", reply)
            return {"reply": reply, "steps": steps, "ddl_suggestion": None, "design_request": None}

        tool_match = _TOOL_RE.search(response)
        if not tool_match:
            return _finish(conversation_id, response, steps)

        tool_name = tool_match.group(1)
        args_text = tool_match.group(2).strip()
        messages.append(_text_message("assistant", response))

        try:
            args = json.loads(args_text) if args_text else {}
        except json.JSONDecodeError as exc:
            json_fail_count += 1
            error_obs = json.dumps({"error": f"JSON 解析失敗：{str(exc)[:200]}"}, ensure_ascii=False)
            messages.append(_text_message("user", f'<OBSERVATION tool="{tool_name}">{error_obs}</OBSERVATION>'))
            session_store.add_message(conversation_id, "tool", json.dumps(
                {"tool": tool_name, "args": None, "observation": error_obs}, ensure_ascii=False))
            steps.append({"tool": tool_name, "args": None, "result_summary": "工具參數 JSON 解析失敗",
                         "result": {"error": "invalid JSON"}})
            if json_fail_count >= MAX_JSON_FAILS:
                reply = "抱歉，工具呼叫的參數格式持續錯誤，請換個方式描述你的需求。"
                session_store.add_message(conversation_id, "ai", reply)
                return {"reply": reply, "steps": steps, "ddl_suggestion": None, "design_request": None}
            continue

        json_fail_count = 0
        result = tool_registry.dispatch(tool_name, args, ctx)
        result = _cap_rows(result)
        obs_text = _obs_text(result)
        messages.append(_text_message("user", f'<OBSERVATION tool="{tool_name}">{obs_text}</OBSERVATION>'))
        session_store.add_message(conversation_id, "tool", json.dumps(
            {"tool": tool_name, "args": args, "observation": obs_text}, ensure_ascii=False))
        steps.append({"tool": tool_name, "args": args, "result_summary": _summarize(result), "result": result})

    reply = "已達到單回合最大工具呼叫次數，以下是目前已知的資訊，如需進一步協助請再詢問一次。"
    session_store.add_message(conversation_id, "ai", reply)
    return {"reply": reply, "steps": steps, "ddl_suggestion": None, "design_request": None}


def _finish(conversation_id: str, response: str, steps: list[dict]) -> dict:
    """No <TOOL> tag in the response → this is the final reply. Extract
    <FINAL>, then <DDL_SUGGESTION>/<DESIGN_REQUEST> out of it, persist the
    clean text, and return the structured result."""
    final_match = _FINAL_RE.search(response)
    text = final_match.group(1).strip() if final_match else response.strip()

    ddl_match = _DDL_TAG_RE.search(text)
    ddl = None
    if ddl_match:
        ddl = {"db": ddl_match.group(1) or None, "sql": ddl_match.group(2).strip()}

    design_match = _DESIGN_TAG_RE.search(text)
    design_request = design_match.group(1).strip() if design_match else None

    clean_text = _DESIGN_TAG_RE.sub("", _DDL_TAG_RE.sub("", text)).strip()

    session_store.add_message(conversation_id, "ai", clean_text)
    return {"reply": clean_text, "steps": steps, "ddl_suggestion": ddl, "design_request": design_request}
