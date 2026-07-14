"""使用者帳號與 refresh token 的 repository 函式（Phase 7 認證）。

`RefreshToken` 是本次新增的資料表，定義於此檔（而非 `app/repos/models.py`——
依任務範圍規定既有內容不可更動），沿用同一個 declarative `Base`，schema 由
`alembic/versions/0002_refresh_tokens.py` 建立。
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.repos.models import Base, User


def _now() -> datetime:
    return datetime.now(UTC)


class RefreshToken(Base):
    """已簽發的 refresh token（雜湊儲存，不留明文），支援登出撤銷。"""

    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("idx_refresh_tokens_user_id", "user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


# -- users --------------------------------------------------------------------


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    """依 email 取得使用者，不存在回傳 None。"""
    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    """依 id 取得使用者，不存在回傳 None。"""
    return await db.get(User, user_id)


async def create_user(
    db: AsyncSession, *, email: str, password_hash: str, role: str = "user"
) -> User:
    """建立一個新使用者。"""
    record = User(email=email, password_hash=password_hash, role=role)
    db.add(record)
    await db.flush()
    return record


# -- refresh tokens -------------------------------------------------------------


async def create_refresh_token(
    db: AsyncSession, *, user_id: uuid.UUID, token_hash: str, expires_at: datetime
) -> RefreshToken:
    """簽發一筆新的 refresh token 紀錄（`token_hash` 為 SHA-256 雜湊，非明文）。"""
    record = RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
    db.add(record)
    await db.flush()
    return record


async def get_refresh_token_by_hash(db: AsyncSession, token_hash: str) -> RefreshToken | None:
    """依雜湊值取得 refresh token 紀錄，不存在回傳 None。"""
    stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def revoke_refresh_token(db: AsyncSession, token_hash: str) -> bool:
    """撤銷指定的 refresh token；不存在或已撤銷回傳 False（登出操作維持冪等）。"""
    record = await get_refresh_token_by_hash(db, token_hash)
    if record is None or record.revoked_at is not None:
        return False
    record.revoked_at = _now()
    await db.flush()
    return True
