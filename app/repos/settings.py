"""`app_settings` 表的 repository 函式（平台層級鍵值設定，例如 LLM CapabilityProfile）。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.repos.models import AppSetting


async def get_setting(db: AsyncSession, key: str) -> AppSetting | None:
    """依 key 取得設定。"""
    return await db.get(AppSetting, key)


async def set_setting(db: AsyncSession, key: str, value_json) -> AppSetting:
    """寫入或覆蓋設定值（upsert）。"""
    record = await get_setting(db, key)
    if record is None:
        record = AppSetting(key=key, value_json=value_json)
        db.add(record)
    else:
        record.value_json = value_json
    await db.flush()
    return record


async def delete_setting(db: AsyncSession, key: str) -> bool:
    """刪除設定，回傳是否有刪到資料。"""
    record = await get_setting(db, key)
    if record is None:
        return False
    await db.delete(record)
    await db.flush()
    return True


async def list_settings(db: AsyncSession) -> list[AppSetting]:
    """列出所有設定。"""
    result = await db.execute(select(AppSetting).order_by(AppSetting.key.asc()))
    return list(result.scalars().all())
