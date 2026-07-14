"""FastAPI 共用依賴：資料庫 session、管理員權杖檢查、JWT 認證。"""

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Annotated

from fastapi import Cookie, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.repos import activity as activity_repo
from app.repos.db import get_session_factory
from app.services.auth_service import AuthError, CurrentUser, decode_access_token

if TYPE_CHECKING:
    from app.repos.models import SessionRecord


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


async def get_current_user(
    authorization: str | None = Header(default=None),
    access_token_cookie: str | None = Cookie(default=None, alias="access_token"),
) -> CurrentUser | None:
    """`AUTH_ENABLED=false`（預設）時直接回傳 None、不擋任何請求，維持 v0.5
    匿名行為；`true` 時要求 `Authorization: Bearer <token>` 或 `access_token`
    cookie，缺少或驗證失敗一律 401。"""
    if not get_settings().auth_enabled:
        return None

    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[len("bearer ") :].strip()
    elif access_token_cookie:
        token = access_token_cookie

    if not token:
        raise HTTPException(status_code=401, detail="缺少認證憑證")

    try:
        return decode_access_token(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


async def require_user(
    current_user: Annotated[CurrentUser | None, Depends(get_current_user)],
) -> CurrentUser:
    """要求已登入（`AUTH_ENABLED=false` 時 `get_current_user` 恆回傳 None，
    因此本依賴恆 401——設計上給『不論 AUTH_ENABLED 都必須登入』的端點使用）。"""
    if current_user is None:
        raise HTTPException(status_code=401, detail="需要登入")
    return current_user


async def check_session_access(
    db: AsyncSession, session: "SessionRecord", current_user: CurrentUser | None
) -> None:
    """session 所有權驗證（`AUTH_ENABLED=true` 時才生效，見 docs/security_design.md 第三章）：
    非本人且非 admin 一律 403；admin 存取他人 session 記錄 `admin.session_access` audit log。
    供所有以 `/sessions/{id}` 為範圍的 router 共用。"""
    if not get_settings().auth_enabled or current_user is None:
        return
    if current_user.role != "admin" and session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="無權限存取此 session")
    if current_user.role == "admin" and session.user_id != current_user.id:
        await activity_repo.log_activity(
            db,
            "admin.session_access",
            {"session_id": str(session.id), "admin_id": str(current_user.id)},
        )


async def require_admin_role(
    current_user: Annotated[CurrentUser | None, Depends(get_current_user)],
    x_admin_token: str | None = Header(default=None),
) -> None:
    """change-requests 審批的管理員檢查：`AUTH_ENABLED=false` 時完全比照舊
    `ADMIN_TOKEN` 機制（`require_admin`）；`true` 時改用 JWT `role=admin`
    （`get_current_user` 已確保未帶合法 token 一律 401，此處只再檢查角色）。"""
    if not get_settings().auth_enabled:
        await require_admin(x_admin_token)
        return
    if current_user is None or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="需要 admin 角色")
