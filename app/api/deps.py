"""FastAPI 共用依賴：資料庫 session 與管理員權杖檢查。"""

from collections.abc import AsyncIterator

from fastapi import Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.repos.db import get_session_factory


async def get_db() -> AsyncIterator[AsyncSession]:
    """每個 request 一個 AsyncSession；正常結束 commit、例外 rollback。"""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    """ADMIN_TOKEN 過渡機制（Phase 7 認證上線前）：
    未設定 ADMIN_TOKEN → 403（提示需先設定）；header 不符 → 401。"""
    admin_token = get_settings().admin_token
    if not admin_token:
        raise HTTPException(status_code=403, detail="伺服器未設定 ADMIN_TOKEN，審批功能停用")
    if x_admin_token != admin_token:
        raise HTTPException(status_code=401, detail="X-Admin-Token 不正確")
