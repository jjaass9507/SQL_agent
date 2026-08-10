"""Interviewer 服務：多輪需求收集對話。

每輪流程：
1. 使用者訊息先落地（messages repo），再從 DB 依序重建完整多輪歷史
   （不維護記憶體 transcript，重啟或多 worker 皆可從 DB 還原）。
2. system prompt 固定讀自 `app/llm/prompts/interviewer.txt`；若本輪需要注入既有
   DB 結構（見 `_should_inject_context`），附加 `db_introspect.format_context()`
   文字。
3. 呼叫 `LLMProvider.chat(response_model=InterviewTurn)` 一次拿到文字回覆與
   結構化 tables/summary。
4. `tables` 非空且**通過提案閘門**（見 `_may_finalize`）→ 寫入新版本快照、
   session phase 轉為 "confirming"；未通過則本輪只是「提案」，把結構附在
   回覆文字後面請使用者確認，不落版本、不轉 phase。

既有 DB context 注入是 sticky 的：一旦本輪判定「動到現有表」（使用者訊息提及
既有表名，或本輪產出的 tables 與既有表同名／FK 指向既有表），往後每輪都持續
注入，直到 session 結束。旗標是 `sessions.inject_db_context`；它原本借用
`app_settings`（key 用 session id 命名空間），2026-08 隨「不得改動 models.py」
公約解除而收回正規欄位（遷移 `0004`，含既有資料搬遷）。
"""

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.provider import LLMProvider
from app.llm.types import Message as LLMMessage
from app.repos import messages as messages_repo
from app.repos import sessions as sessions_repo
from app.repos import versions as versions_repo
from app.repos.models import Message as MessageRecord
from app.repos.models import SessionRecord
from app.rules import db_introspect
from app.rules.spec_models import TableSpec, tables_from_json

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "llm" / "prompts" / "interviewer.txt"
_ROLE_TO_LLM = {"user": "user", "ai": "assistant"}

# 提案訊息的固定標記：既是給使用者看的分隔線，也是「這輪已提過案」的判斷依據
# （不另外存狀態，數歷史裡有幾則提案就知道進行到哪一步）。
_PROPOSAL_MARK = "—— 以下是目前規劃的結構（尚未定案）——"

# 模型始終不把 user_confirmed 設為 true 時的保險：提案累積到這個次數就放行。
# 沒有這道保險，指令遵循力差的模型會讓使用者不管回覆什麼都永遠進不了確認頁。
_MAX_PROPOSALS = 2


class InterviewTurn(BaseModel):
    """Interviewer 單輪 structured output。"""

    reply: str
    tables: list[TableSpec] | None = None
    summary: list[str] | None = None
    user_confirmed: bool = False


@lru_cache
def _base_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


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


def _should_inject_context(session: SessionRecord, user_content: str) -> bool:
    if not session.context_tables_json:
        return False
    if session.inject_db_context:
        return True
    return _mentions_existing_table(user_content, session.context_tables_json)


def _proposal_count(history: list[MessageRecord]) -> int:
    """歷史中已經給使用者看過幾次提案。"""
    return sum(1 for m in history if m.role == "ai" and _PROPOSAL_MARK in m.content)


def _may_finalize(turn: "InterviewTurn", proposals: int) -> bool:
    """本輪的 tables 可否定案（落版本、進確認頁）。

    要先提過案（使用者看得到具體結構）、且使用者明確同意過，才算討論確認完成：
    - 尚未提過案 → 一律不定案（一句需求就直接產出設計正是要擋掉的情況）
    - 提過一次 → 模型回報 user_confirmed 才定案
    - 提過 `_MAX_PROPOSALS` 次 → 直接放行（模型不會回報同意時的保險）
    """
    return proposals >= 1 and (turn.user_confirmed or proposals >= _MAX_PROPOSALS)


def _format_proposal(tables: list[TableSpec]) -> str:
    """把提案的結構排版成純文字附在回覆後面。

    由我們自己排版而不是仰賴模型在 reply 裡寫清楚——模型常把設計只放進結構化
    的 tables 欄位，reply 只留一句「設計完成」，那樣擋下 tables 後使用者就什麼
    都看不到，也無從同意。
    """
    lines = [_PROPOSAL_MARK]
    for table in tables:
        title = table.table_name
        if table.description:
            title = f"{title}（{table.description}）"
        lines.append(f"\n{title}")
        for column in table.columns:
            type_str = f"{column.data_type}({column.length})" if column.length else column.data_type
            marks = []
            if column.is_primary_key:
                marks.append("PK")
            if column.is_foreign_key and column.references:
                marks.append(f"FK→{column.references}")
            if column.is_unique:
                marks.append("UNIQUE")
            if not column.nullable:
                marks.append("NOT NULL")
            mark_str = f"  [{', '.join(marks)}]" if marks else ""
            desc_str = f"  — {column.description}" if column.description else ""
            lines.append(f"  - {column.name}: {type_str}{mark_str}{desc_str}")
    lines.append("\n以上結構是否可以？回覆「可以」我就產出正式設計；要調整的地方請直接說。")
    return "\n".join(lines)


def _to_llm_message(record: MessageRecord) -> LLMMessage:
    return {"role": _ROLE_TO_LLM.get(record.role, record.role), "content": record.content}


async def run_turn(
    db: AsyncSession, provider: LLMProvider, session: SessionRecord, user_content: str
) -> InterviewTurn:
    """處理一輪對話，回傳結構化結果。

    副作用：寫入 user/ai 訊息，視情況建立新版本快照並轉換 phase。
    """
    await messages_repo.add_message(db, session.id, "user", user_content)

    inject_context = _should_inject_context(session, user_content)
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

    proposed_tables = turn.tables
    if proposed_tables and not _may_finalize(turn, _proposal_count(history)):
        # 尚未取得使用者同意：本輪降級成提案，結構附在回覆文字裡給使用者看。
        turn.reply = f"{turn.reply}\n\n{_format_proposal(proposed_tables)}"
        turn.tables = None
        turn.summary = None

    await messages_repo.add_message(db, session.id, "ai", turn.reply)

    touches_existing = bool(
        proposed_tables and session.context_tables_json
        and _touches_existing_tables(proposed_tables, session.context_tables_json)
    )
    if (inject_context or touches_existing) and not session.inject_db_context:
        await sessions_repo.update_session(db, session.id, inject_db_context=True)

    if turn.tables:
        await versions_repo.create_version(
            db,
            session.id,
            tables_json=[t.model_dump() for t in turn.tables],
            key_points_json=turn.summary,
        )
        await sessions_repo.update_session(db, session.id, phase="confirming")

    return turn
