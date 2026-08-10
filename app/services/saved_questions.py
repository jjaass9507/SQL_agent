"""常用問題清單與「已核可」標記。

由來：業務單位每個月要交固定幾份報表，每次都得重新描述一次同樣的問題。
存下來一鍵重跑，是「不用每天麻煩別人」這條路上的最後一段。

「已核可」是討論裡長出來的東西——把「看不看得懂 SQL」與「有沒有人覆核過」
拆成兩個獨立維度之後的產物。查詢結果旁固定顯示的「尚未經人工覆核」是底線，
核可標記才是讓那句警示不會變成裝飾的另一半。

**核可會過期**：問題今天核可了，底層結構或口徑之後被改掉，提問的人完全不會
發現，還會繼續拿舊口徑的數字去交報表。因此每項都記「上次確認的日期」，
超過有效期就標成需要重新確認——這比「自動重跑」更重要，數字自動更新但口徑
已經失效，比沒有自動更新更危險。
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.repos import settings as settings_repo

SAVED_QUESTIONS_KEY = "saved_questions"

# 超過這麼久沒有人重新確認過口徑，就標成需要重新確認。90 天是「一季」的直覺
# 長度：多數報表口徑的調整週期不會比這更短。
APPROVAL_VALID_DAYS = 90

MAX_QUESTIONS_PER_DB = 50


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _is_stale(entry: dict) -> bool:
    """核可過期 = 已核可，但距離上次確認超過有效期。"""
    if not entry.get("approved") or not entry.get("approved_at"):
        return False
    try:
        approved_at = datetime.fromisoformat(entry["approved_at"])
    except (TypeError, ValueError):
        return True
    return datetime.now(UTC) - approved_at > timedelta(days=APPROVAL_VALID_DAYS)


def _present(entry: dict) -> dict:
    """加上前端需要、但不必落地儲存的衍生欄位。"""
    return {**entry, "stale": _is_stale(entry)}


async def _load(db: AsyncSession) -> dict[str, list[dict]]:
    setting = await settings_repo.get_setting(db, SAVED_QUESTIONS_KEY)
    return dict((setting.value_json if setting and setting.value_json else {}) or {})


async def list_questions(db: AsyncSession, db_name: str) -> list[dict]:
    stored = await _load(db)
    return [_present(e) for e in stored.get(db_name, [])]


async def save_question(
    db: AsyncSession,
    db_name: str,
    question: str,
    sql: str,
    question_id: str | None = None,
) -> dict:
    """新增或更新一則常用問題。

    更新既有問題且**語法有變**時撤銷核可：改了語法就等於換了口徑，
    先前那個「我確認過」不能繼續掛在新的語法上。
    """
    stored = await _load(db)
    entries = list(stored.get(db_name, []))

    existing = next((e for e in entries if e["id"] == question_id), None) if question_id else None
    if existing is not None:
        entry = dict(existing)
        if entry.get("sql") != sql:
            entry.update(approved=False, approved_by=None, approved_at=None)
        entry.update(question=question, sql=sql, updated_at=_now())
        entries = [entry if e["id"] == entry["id"] else e for e in entries]
    else:
        entry = {
            "id": str(uuid.uuid4()),
            "question": question,
            "sql": sql,
            "approved": False,
            "approved_by": None,
            "approved_at": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        entries.insert(0, entry)

    stored[db_name] = entries[:MAX_QUESTIONS_PER_DB]
    await settings_repo.set_setting(db, SAVED_QUESTIONS_KEY, stored)
    return _present(entry)


async def approve_question(
    db: AsyncSession, db_name: str, question_id: str, actor: str
) -> dict | None:
    """標記為「口徑已確認」。記下是誰、什麼時候——否則無從判斷還算不算數。"""
    stored = await _load(db)
    entries = list(stored.get(db_name, []))
    entry = next((e for e in entries if e["id"] == question_id), None)
    if entry is None:
        return None

    entry = {**entry, "approved": True, "approved_by": actor, "approved_at": _now()}
    stored[db_name] = [entry if e["id"] == question_id else e for e in entries]
    await settings_repo.set_setting(db, SAVED_QUESTIONS_KEY, stored)
    return _present(entry)


async def delete_question(db: AsyncSession, db_name: str, question_id: str) -> bool:
    stored = await _load(db)
    entries = stored.get(db_name, [])
    remaining = [e for e in entries if e["id"] != question_id]
    if len(remaining) == len(entries):
        return False
    stored[db_name] = remaining
    await settings_repo.set_setting(db, SAVED_QUESTIONS_KEY, stored)
    return True
