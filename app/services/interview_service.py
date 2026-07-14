"""Interviewer 服務：多輪需求收集對話。

每輪流程：
1. 使用者訊息先落地（messages repo），再從 DB 依序重建完整多輪歷史
   （不維護記憶體 transcript，重啟或多 worker 皆可從 DB 還原）。
2. system prompt 固定讀自 `app/llm/prompts/interviewer.txt`；若本輪需要注入既有
   DB 結構（見 `_should_inject_context`），附加 `db_introspect.format_context()`
   文字。
3. 呼叫 `LLMProvider.chat(response_model=InterviewTurn)` 一次拿到文字回覆與
   結構化 tables/summary。
4. `tables` 非空 → 寫入新版本快照、session phase 轉為 "confirming"。

既有 DB context 注入是 sticky 的：一旦本輪判定「動到現有表」（使用者訊息提及
既有表名，或本輪產出的 tables 與既有表同名／FK 指向既有表），往後每輪都持續
注入，直到 session 結束。sticky 旗標無合適欄位可存（不得改動 app/repos/models.py），
借用 `app_settings`（key 用 session id 命名空間）記錄。
"""

from functools import lru_cache
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.provider import LLMProvider
from app.llm.types import Message as LLMMessage
from app.repos import messages as messages_repo
from app.repos import sessions as sessions_repo
from app.repos import settings as settings_repo
from app.repos import versions as versions_repo
from app.repos.models import Message as MessageRecord
from app.repos.models import SessionRecord
from app.rules import db_introspect
from app.rules.spec_models import TableSpec, tables_from_json

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "llm" / "prompts" / "interviewer.txt"
_ROLE_TO_LLM = {"user": "user", "ai": "assistant"}


class InterviewTurn(BaseModel):
    """Interviewer 單輪 structured output。"""

    reply: str
    tables: list[TableSpec] | None = None
    summary: list[str] | None = None


@lru_cache
def _base_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _sticky_key(session_id: UUID) -> str:
    return f"session_context_sticky:{session_id}"


async def _get_sticky(db: AsyncSession, session_id: UUID) -> bool:
    record = await settings_repo.get_setting(db, _sticky_key(session_id))
    return bool(record.value_json) if record else False


async def _set_sticky(db: AsyncSession, session_id: UUID) -> None:
    await settings_repo.set_setting(db, _sticky_key(session_id), True)


def _mentions_existing_table(text: str, tables_json: list[dict]) -> bool:
    lowered = text.lower()
    return any(
        name in lowered
        for t in tables_json
        if (name := str(t.get("table_name", "")).lower())
    )


def _touches_existing_tables(new_tables: list[TableSpec], existing_tables_json: list[dict]) -> bool:
    existing_names = {str(t.get("table_name", "")).lower() for t in existing_tables_json}
    for table in new_tables:
        if table.table_name.lower() in existing_names:
            return True
        for column in table.columns:
            if column.is_foreign_key and column.references:
                ref_table = column.references.split(".")[0].lower()
                if ref_table in existing_names:
                    return True
    return False


async def _should_inject_context(
    db: AsyncSession, session: SessionRecord, user_content: str
) -> bool:
    if not session.context_tables_json:
        return False
    if await _get_sticky(db, session.id):
        return True
    return _mentions_existing_table(user_content, session.context_tables_json)


def _to_llm_message(record: MessageRecord) -> LLMMessage:
    return {"role": _ROLE_TO_LLM.get(record.role, record.role), "content": record.content}


async def run_turn(
    db: AsyncSession, provider: LLMProvider, session: SessionRecord, user_content: str
) -> InterviewTurn:
    """處理一輪對話，回傳結構化結果。

    副作用：寫入 user/ai 訊息，視情況建立新版本快照並轉換 phase。
    """
    await messages_repo.add_message(db, session.id, "user", user_content)

    inject_context = await _should_inject_context(db, session, user_content)
    system_prompt = _base_system_prompt()
    if inject_context and session.context_tables_json:
        existing_tables = tables_from_json(session.context_tables_json)
        context_text = db_introspect.format_context(existing_tables)
        if context_text:
            system_prompt = f"{system_prompt}\n\n{context_text}"

    history = await messages_repo.list_messages(db, session.id)
    llm_messages: list[LLMMessage] = [{"role": "system", "content": system_prompt}]
    llm_messages.extend(_to_llm_message(m) for m in history)

    result = await provider.chat(llm_messages, response_model=InterviewTurn)
    turn: InterviewTurn = result.parsed

    await messages_repo.add_message(db, session.id, "ai", turn.reply)

    touches_existing = bool(
        turn.tables and session.context_tables_json
        and _touches_existing_tables(turn.tables, session.context_tables_json)
    )
    if (inject_context or touches_existing) and not await _get_sticky(db, session.id):
        await _set_sticky(db, session.id)

    if turn.tables:
        await versions_repo.create_version(
            db,
            session.id,
            tables_json=[t.model_dump() for t in turn.tables],
            key_points_json=turn.summary,
        )
        await sessions_repo.update_session(db, session.id, phase="confirming")

    return turn
