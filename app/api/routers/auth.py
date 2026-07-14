"""認證 API：`POST /auth/login`、`POST /auth/refresh`、`POST /auth/logout`。

依 `docs/security_design.md` 第二章：HS256 JWT、access 15 分/refresh 7 天、
refresh token 存 DB 支援登出撤銷。Token 同時以 HttpOnly Cookie（瀏覽器）與
JSON body（非瀏覽器客戶端）回傳。這三個端點本身不受 `AUTH_ENABLED` 開關限制
（供前端/測試提前串接），但只有 `AUTH_ENABLED=true` 時，其他端點才會要求／
驗證這裡簽發的 token（見 `app/api/deps.py` 的 `get_current_user`）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.config import get_settings
from app.repos import activity as activity_repo
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

DbDep = Annotated[AsyncSession, Depends(get_db)]


class LoginRequest(BaseModel):
    # 用一般字串（長度上限防濫用），不用 EmailStr——避免引入 email-validator 依賴；
    # email 格式正確性由建帳號流程（scripts/create_user.py）負責。
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    """省略時改讀 `refresh_token` cookie（見 `_extract_refresh_token`）。"""

    refresh_token: str | None = None


class LogoutRequest(BaseModel):
    """省略時改讀 `refresh_token` cookie（見 `_extract_refresh_token`）。"""

    refresh_token: str | None = None


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _extract_refresh_token(
    request: Request, payload: RefreshRequest | LogoutRequest | None
) -> str | None:
    if payload and payload.refresh_token:
        return payload.refresh_token
    return request.cookies.get("refresh_token")


def _set_auth_cookies(response: Response, *, access_token: str, expires_in: int) -> None:
    """`HttpOnly; Secure; SameSite=Lax`（見 security_design.md 第六章）。"""
    response.set_cookie(
        "access_token",
        access_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=expires_in,
    )


def _set_refresh_cookie(response: Response, *, refresh_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        "refresh_token",
        refresh_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=settings.jwt_refresh_ttl_days * 24 * 3600,
    )


async def _enforce_login_rate_limit(request: Request) -> None:
    """`/auth/login` 較嚴格的 per-IP 滑動視窗限流（上限見 `settings.auth_rate_limit_login_*`）。"""
    settings = get_settings()
    ip = _client_ip(request) or "unknown"
    allowed = auth_service.login_rate_limiter.check(
        ip,
        max_requests=settings.auth_rate_limit_login_max,
        window_seconds=settings.auth_rate_limit_login_window_seconds,
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="登入嘗試過於頻繁，請稍後再試")


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(_enforce_login_rate_limit)],
)
async def login(
    payload: LoginRequest, request: Request, response: Response, db: DbDep
) -> TokenResponse:
    ip = _client_ip(request)
    try:
        result = await auth_service.login(db, payload.email, payload.password)
    except auth_service.AuthError as exc:
        await activity_repo.log_activity(
            db, "auth.login_failed", {"email": payload.email, "ip": ip}
        )
        # get_db 依賴在例外時 rollback；audit log 必須先 commit 才不會被一併回滾。
        await db.commit()
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    _set_auth_cookies(response, access_token=result.access_token, expires_in=result.expires_in)
    _set_refresh_cookie(response, refresh_token=result.refresh_token)
    await activity_repo.log_activity(db, "auth.login", {"user_id": str(result.user.id), "ip": ip})
    return TokenResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        expires_in=result.expires_in,
    )


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh(
    request: Request, response: Response, db: DbDep, payload: RefreshRequest | None = None
) -> AccessTokenResponse:
    token = _extract_refresh_token(request, payload)
    if not token:
        raise HTTPException(status_code=401, detail="缺少 refresh token")
    try:
        result = await auth_service.refresh_access_token(db, token)
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    _set_auth_cookies(response, access_token=result.access_token, expires_in=result.expires_in)
    return AccessTokenResponse(access_token=result.access_token, expires_in=result.expires_in)


@router.post("/logout", status_code=204)
async def logout(
    request: Request, response: Response, db: DbDep, payload: LogoutRequest | None = None
) -> None:
    token = _extract_refresh_token(request, payload)
    if token:
        await auth_service.logout(db, token)
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    await activity_repo.log_activity(db, "auth.logout", {"ip": _client_ip(request)})
