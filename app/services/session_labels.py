"""Session 的標籤與釘選。

由來：後端工程師手上同時三四個案子，首頁只能用名稱搜尋與狀態篩選，分不出哪個
是哪個 PM 的需求——驗收時他點名這是「還沒做的裡面最痛的」。

存在 `app_settings` 的單一 JSON。當初這樣寫是因為「不得改動 models.py」的凍結，
但即使凍結已解除（見 HANDOFF.md §6.1）也沒有立刻搬家的理由：標籤是每個 session
幾個字串的量級，撐不起一張獨立資料表。**真正該搬的訊號是查詢需求**——當「列出
所有標成 PM-陳 的 session」需要 SQL 而不是全撈進記憶體過濾時，就該建表。
那時這裡是唯一需要改的地方。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.repos import settings as settings_repo

SESSION_LABELS_KEY = "session_labels"

MAX_TAGS = 8
MAX_TAG_LENGTH = 20


def _clean_tags(tags: list[str] | None) -> list[str]:
    """去空白、去空字串、去重（保留順序），並限制數量與長度。"""
    seen: list[str] = []
    for raw in tags or []:
        tag = str(raw).strip()[:MAX_TAG_LENGTH]
        if tag and tag not in seen:
            seen.append(tag)
    return seen[:MAX_TAGS]


async def _load(db: AsyncSession) -> dict:
    setting = await settings_repo.get_setting(db, SESSION_LABELS_KEY)
    return dict((setting.value_json if setting and setting.value_json else {}) or {})


async def get_labels(db: AsyncSession, session_id) -> dict:
    entry = (await _load(db)).get(str(session_id)) or {}
    return {"tags": entry.get("tags") or [], "pinned": bool(entry.get("pinned"))}


async def get_labels_map(db: AsyncSession) -> dict[str, dict]:
    """一次取回全部——首頁清單要為每一筆附上標籤，不能每筆各查一次。"""
    return await _load(db)


async def set_labels(
    db: AsyncSession, session_id, *, tags: list[str] | None = None, pinned: bool | None = None
) -> dict:
    """更新標籤／釘選。未提供的欄位保持原值——只改標籤不該把釘選一起洗掉。"""
    stored = await _load(db)
    key = str(session_id)
    entry = dict(stored.get(key) or {})

    if tags is not None:
        entry["tags"] = _clean_tags(tags)
    if pinned is not None:
        entry["pinned"] = bool(pinned)

    if entry.get("tags") or entry.get("pinned"):
        stored[key] = entry
    else:
        stored.pop(key, None)  # 兩者都空就不留空殼

    await settings_repo.set_setting(db, SESSION_LABELS_KEY, stored)
    return {"tags": entry.get("tags") or [], "pinned": bool(entry.get("pinned"))}


async def forget(db: AsyncSession, session_id) -> None:
    """session 被刪除時一併清掉標籤，不留孤兒資料。"""
    stored = await _load(db)
    if stored.pop(str(session_id), None) is not None:
        await settings_repo.set_setting(db, SESSION_LABELS_KEY, stored)
