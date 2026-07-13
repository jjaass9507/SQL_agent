"""`schema_versions` 表的 repository 函式。每個 session 最多保留 10 版快照，超過刪最舊。"""

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.repos.models import SchemaVersion

MAX_VERSIONS_PER_SESSION = 10


async def create_version(
    db: AsyncSession,
    session_id: uuid.UUID,
    tables_json: list | None = None,
    key_points_json: list | None = None,
) -> SchemaVersion:
    """新增一版 schema 快照（version_num 自動遞增），超過上限時刪除最舊的版本。"""
    max_num = await db.scalar(
        select(func.max(SchemaVersion.version_num)).where(SchemaVersion.session_id == session_id)
    )
    next_num = (max_num or 0) + 1

    record = SchemaVersion(
        session_id=session_id,
        version_num=next_num,
        tables_json=tables_json,
        key_points_json=key_points_json,
    )
    db.add(record)
    await db.flush()

    await _enforce_version_cap(db, session_id)
    return record


async def _enforce_version_cap(db: AsyncSession, session_id: uuid.UUID) -> None:
    """刪除超出 `MAX_VERSIONS_PER_SESSION` 的最舊版本。"""
    count = await db.scalar(
        select(func.count())
        .select_from(SchemaVersion)
        .where(SchemaVersion.session_id == session_id)
    )
    overflow = (count or 0) - MAX_VERSIONS_PER_SESSION
    if overflow <= 0:
        return

    oldest_ids = await db.execute(
        select(SchemaVersion.id)
        .where(SchemaVersion.session_id == session_id)
        .order_by(SchemaVersion.version_num.asc())
        .limit(overflow)
    )
    ids = [row[0] for row in oldest_ids.all()]
    if ids:
        await db.execute(delete(SchemaVersion).where(SchemaVersion.id.in_(ids)))
        await db.flush()


async def list_versions(db: AsyncSession, session_id: uuid.UUID) -> list[SchemaVersion]:
    """依版本號由新到舊列出某 session 的所有快照。"""
    stmt = (
        select(SchemaVersion)
        .where(SchemaVersion.session_id == session_id)
        .order_by(SchemaVersion.version_num.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_version(
    db: AsyncSession, session_id: uuid.UUID, version_num: int
) -> SchemaVersion | None:
    """取得指定版本號的快照。"""
    stmt = select(SchemaVersion).where(
        SchemaVersion.session_id == session_id, SchemaVersion.version_num == version_num
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_latest_version(db: AsyncSession, session_id: uuid.UUID) -> SchemaVersion | None:
    """取得最新版本的快照。"""
    stmt = (
        select(SchemaVersion)
        .where(SchemaVersion.session_id == session_id)
        .order_by(SchemaVersion.version_num.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
