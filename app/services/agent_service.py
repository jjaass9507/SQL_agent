"""DB Agent 服務：原生 function calling 的 ReAct 工具迴圈 + HITL 終止工具。

單一全域 agent 對話（session mode 沿用既有 CheckConstraint 允許的 "design"；
其 id 存於 app_settings，key 見 `_AGENT_SESSION_SETTING_KEY`）。每回合從
`messages` repo 重建完整 transcript：工具呼叫/結果以 role="ai"、content 為
JSON 字串（`{"type": "tool_call"|"tool_result", ...}`）持久化——`messages.role`
的 CheckConstraint 只允許 'user'/'ai'（app/repos/models.py 不在本階段可改動
範圍內），因此不新增 role="tool"，改以內容型別區分，重建時再展開成原生
`assistant(tool_calls)` + `tool` 訊息對送給 LLM。

`propose_ddl` 是 terminal 工具：呼叫後立即結束本回合，回覆由本模組合成
（不再呼叫 LLM）。「新建資料表」意圖由 prompt 要求模型在最終回覆文字附上
`[[DESIGN_REQUEST]]...[[/DESIGN_REQUEST]]` 標記，本模組解析後拆成
`design_request` 欄位並從顯示文字中移除；不自建 session，只回傳給前端。
"""

import json
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.capabilities import CapabilityProfile
from app.llm.provider import LLMProvider
from app.repos import messages as messages_repo
from app.repos import sessions as sessions_repo
from app.repos import settings as settings_repo
from app.repos.models import Message
from app.services import tool_registry

MAX_STEPS = 8
MAX_OBS_ROWS = 20
MAX_OBS_CHARS = 4_000
MAX_MESSAGES_CHARS = 24_000

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "agent.txt"
_AGENT_SESSION_SETTING_KEY = "agent_session_id"
_CAPABILITY_SETTING_KEY = "llm_capability_profile"

_DESIGN_REQUEST_RE = re.compile(r"\[\[DESIGN_REQUEST\]\](.*?)\[\[/DESIGN_REQUEST\]\]", re.DOTALL)

_MAX_STEPS_REPLY = (
    "已達到單回合最大工具呼叫次數，以下是目前已知的資訊，如需進一步協助請再詢問一次。"
)


# ── 全域 agent session / provider ─────────────────────────────────────────


async def _get_or_create_agent_session_id(db: AsyncSession) -> uuid.UUID:
    """單一全域 DB Agent 對話 session；id 存於 app_settings，跨程序/重啟共用。"""
    setting = await settings_repo.get_setting(db, _AGENT_SESSION_SETTING_KEY)
    if setting is not None and setting.value_json:
        return uuid.UUID(setting.value_json)
    record = await sessions_repo.create_session(db, title="DB Agent 對話", mode="design")
    await settings_repo.set_setting(db, _AGENT_SESSION_SETTING_KEY, str(record.id))
    return record.id


async def _build_provider(db: AsyncSession) -> LLMProvider:
    setting = await settings_repo.get_setting(db, _CAPABILITY_SETTING_KEY)
    profile = CapabilityProfile(**setting.value_json) if setting and setting.value_json else None
    return LLMProvider.from_settings(profile=profile)


# ── transcript 編碼／重建 ──────────────────────────────────────────────────


def _encode_tool_call(call_id: str, name: str, args: dict) -> str:
    data = {"type": "tool_call", "id": call_id, "tool": name, "args": args}
    return json.dumps(data, ensure_ascii=False)


def _encode_tool_result(call_id: str, name: str, observation: str) -> str:
    data = {"type": "tool_result", "id": call_id, "tool": name, "observation": observation}
    return json.dumps(data, ensure_ascii=False)


def _decode_ai_content(content: str) -> dict | None:
    """`role="ai"` 的內容若是 `{"type": "tool_call"|"tool_result", ...}` 的 JSON 則回傳
    解析後的 dict，否則（一般文字回覆）回傳 None。"""
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return None
    if isinstance(data, dict) and data.get("type") in ("tool_call", "tool_result"):
        return data
    return None


def _rebuild_messages(records: list[Message]) -> list[dict]:
    """把 DB 中持久化的 messages 展開成原生 Chat Completions 格式的 messages 陣列。"""
    result: list[dict] = []
    for record in records:
        if record.role == "user":
            result.append({"role": "user", "content": record.content})
            continue
        decoded = _decode_ai_content(record.content)
        if decoded is None:
            result.append({"role": "assistant", "content": record.content})
        elif decoded["type"] == "tool_call":
            result.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": decoded["id"],
                    "type": "function",
                    "function": {
                        "name": decoded["tool"],
                        "arguments": json.dumps(decoded["args"], ensure_ascii=False),
                    },
                }],
            })
        else:  # tool_result
            result.append({
                "role": "tool",
                "tool_call_id": decoded["id"],
                "content": decoded["observation"],
            })
    return result


def _msg_chars(m: dict) -> int:
    return len(json.dumps(m, ensure_ascii=False, default=str))


def _trim_to_budget(messages: list[dict]) -> list[dict]:
    """messages 總字數超過預算時，從最舊的訊息開始丟棄。"""
    trimmed = list(messages)
    total = sum(_msg_chars(m) for m in trimmed)
    while total > MAX_MESSAGES_CHARS and len(trimmed) > 1:
        removed = trimmed.pop(0)
        total -= _msg_chars(removed)
    return trimmed


# ── observation 截斷／摘要 ─────────────────────────────────────────────────


def _cap_rows(result: dict) -> dict:
    """每則 observation 最多 20 列。"""
    rows = result.get("rows") if isinstance(result, dict) else None
    if isinstance(rows, list) and len(rows) > MAX_OBS_ROWS:
        result = dict(result)
        result["rows"] = result["rows"][:MAX_OBS_ROWS]
        result["truncated"] = True
    return result


def _obs_text(result: dict) -> str:
    """每則 observation 的 JSON 文字最多 4,000 字。"""
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > MAX_OBS_CHARS:
        text = text[:MAX_OBS_CHARS] + "...(截斷)"
    return text


def _summarize(result: dict) -> str:
    """給前端步驟軌跡看的一行人話摘要。"""
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
    if "proposal_id" in result:
        return f"已建立變更提案 #{str(result['proposal_id'])[:8]}"
    if "databases" in result:
        return f"{len(result.get('databases', []))} 個資料庫"
    return "完成"


def _extract_design_request(text: str) -> tuple[str, str | None]:
    """從最終回覆文字拆出 `[[DESIGN_REQUEST]]...[[/DESIGN_REQUEST]]` 標記。
    回傳 (顯示給使用者的乾淨文字, design_request 摘要或 None)。"""
    match = _DESIGN_REQUEST_RE.search(text)
    if not match:
        return text, None
    design_request = match.group(1).strip()
    clean_text = _DESIGN_REQUEST_RE.sub("", text).strip()
    return clean_text, design_request


# ── 主迴圈 ─────────────────────────────────────────────────────────────────


async def run_agent_turn_stream(
    db: AsyncSession,
    user_message: str,
    db_name: str | None = None,
    *,
    provider: LLMProvider | None = None,
) -> AsyncIterator[dict]:
    """執行一回合原生 function calling 的 ReAct 工具迴圈，以 async generator 即時吐出事件。

    事件為 `{"event": "tool_call"|"tool_result"|"delta"|"turn_done", "data": {...}}`，
    最後一個事件必為 `turn_done`，其 data 為
    `{reply, steps, proposal, design_request}`。
    """
    session_id = await _get_or_create_agent_session_id(db)
    await messages_repo.add_message(db, session_id, "user", user_message)

    history = await messages_repo.list_messages(db, session_id)
    system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    full_messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        *_rebuild_messages(history),
    ]

    provider = provider or await _build_provider(db)
    ctx = tool_registry.ToolContext(db=db, db_name=db_name)
    tool_defs = tool_registry.tool_defs()

    steps: list[dict] = []

    for _ in range(MAX_STEPS):
        full_messages = _trim_to_budget(full_messages)
        result = await provider.chat(full_messages, tools=tool_defs)

        if not result.tool_calls:
            reply, design_request = _extract_design_request(result.text or "")
            await messages_repo.add_message(db, session_id, "ai", reply)
            yield {"event": "delta", "data": {"text": reply}}
            yield {
                "event": "turn_done",
                "data": {
                    "reply": reply,
                    "steps": steps,
                    "proposal": None,
                    "design_request": design_request,
                },
            }
            return

        # 每輪只處理第一個工具呼叫（prompt 已要求模型一次只呼叫一個工具）。
        call = result.tool_calls[0]
        yield {"event": "tool_call", "data": {"tool": call.name, "args": call.arguments}}

        raw_result = await tool_registry.dispatch(call.name, call.arguments, ctx)
        capped = _cap_rows(raw_result)
        obs_text = _obs_text(capped)
        summary = _summarize(capped)
        steps.append({"tool": call.name, "args": call.arguments, "result_summary": summary})
        yield {"event": "tool_result", "data": {"tool": call.name, "result_summary": summary}}

        await messages_repo.add_message(
            db, session_id, "ai", _encode_tool_call(call.id, call.name, call.arguments)
        )
        await messages_repo.add_message(
            db, session_id, "ai", _encode_tool_result(call.id, call.name, obs_text)
        )
        call_arguments = json.dumps(call.arguments, ensure_ascii=False)
        full_messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call_arguments},
            }],
        })
        full_messages.append({"role": "tool", "tool_call_id": call.id, "content": obs_text})

        if call.name == "propose_ddl":
            reply, proposal = _finish_propose_ddl(capped)
            await messages_repo.add_message(db, session_id, "ai", reply)
            yield {"event": "delta", "data": {"text": reply}}
            yield {
                "event": "turn_done",
                "data": {
                    "reply": reply,
                    "steps": steps,
                    "proposal": proposal,
                    "design_request": None,
                },
            }
            return

    await messages_repo.add_message(db, session_id, "ai", _MAX_STEPS_REPLY)
    yield {"event": "delta", "data": {"text": _MAX_STEPS_REPLY}}
    yield {
        "event": "turn_done",
        "data": {
            "reply": _MAX_STEPS_REPLY,
            "steps": steps,
            "proposal": None,
            "design_request": None,
        },
    }


def _finish_propose_ddl(result: dict) -> tuple[str, dict | None]:
    """propose_ddl 是 terminal 工具：迴圈立即結束，不再呼叫 LLM，回覆由本函式合成。"""
    if "error" in result:
        return f"提案未成立：{result['error']}", None
    proposal = {
        "proposal_id": result.get("proposal_id"),
        "dry_run_ok": result.get("dry_run_ok"),
        "status": result.get("status"),
    }
    reply = (
        f"已提交結構變更提案（編號 {proposal['proposal_id']}），"
        "dry-run 驗證通過，等待管理員審核後才會執行。"
    )
    return reply, proposal


async def run_agent_turn(
    db: AsyncSession,
    user_message: str,
    db_name: str | None = None,
    *,
    provider: LLMProvider | None = None,
) -> dict:
    """非串流版本：跑完整回合後回傳最終結果（供 JSON 一次性回應使用）。"""
    final: dict = {}
    async for event in run_agent_turn_stream(db, user_message, db_name, provider=provider):
        if event["event"] == "turn_done":
            final = event["data"]
    return final
