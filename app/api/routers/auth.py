"""認證 API：`POST /auth/login`、`POST /auth/refresh`、`POST /auth/logout`、
`GET /auth/me`、`GET /auth/sso`。

依 `docs/security_design.md` 第二章：HS256 JWT、access 15 分/refresh 7 天、
refresh token 存 DB 支援登出撤銷。Token 同時以 HttpOnly Cookie（瀏覽器）與
JSON body（非瀏覽器客戶端）回傳。這些端點本身不受 `AUTH_ENABLED` 開關限制
（供前端/測試提前串接），但只有 `AUTH_ENABLED=true` 時，其他端點才會要求／
驗證這裡簽發的 token（見 `app/api/deps.py` 的 `get_current_user`）。

`AD_ENABLED=true` 時 `/auth/login` 會先試 AD 登入（見 `app/services/ad_auth.py`），
`AD_SSO_ENABLED=true` 時另外啟用 `/auth/sso`（IIS Windows SSO 自動登入）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.schemas.auth import MeResponse
from app.config import Settings, get_settings
from app.repos import activity as activity_repo
from app.repos import users as users_repo
from app.services import ad_auth, auth_service
from app.services.auth_service import CurrentUser

router = APIRouter(prefix="/auth", tags=["auth"])

DbDep = Annotated[AsyncSession, Depends(get_db)]


class LoginRequest(BaseModel):
    # 用一般字串（長度上限防濫用），不用 EmailStr——避免引入 email-validator 依賴；
    # email 格式正確性由建帳號流程（scripts/create_user.py）負責。
    # `username` 為 AD 登入新增欄位（可為 sAMAccountName／UPN／DOMAIN\user）；
    # 向下相容仍收 `email`，兩者皆提供時 `username` 優先。
    email: str | None = Field(default=None, min_length=3, max_length=320)
    username: str | None = Field(default=None, min_length=1, max_length=320)
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
    identifier = payload.username or payload.email or ""
    try:
        result, source = await auth_service.login_with_credentials(
            db, identifier, payload.password
        )
    except auth_service.ADUnavailableError as exc:
        await activity_repo.log_activity(
            db, "auth.login_failed", {"username": identifier, "ip": ip, "reason": "ad_unavailable"}
        )
        # get_db 依賴在例外時 rollback；audit log 必須先 commit 才不會被一併回滾。
        await db.commit()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except auth_service.AuthError as exc:
        await activity_repo.log_activity(
            db, "auth.login_failed", {"username": identifier, "ip": ip}
        )
        await db.commit()
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    _set_auth_cookies(response, access_token=result.access_token, expires_in=result.expires_in)
    _set_refresh_cookie(response, refresh_token=result.refresh_token)
    await activity_repo.log_activity(
        db, "auth.login", {"user_id": str(result.user.id), "ip": ip, "source": source}
    )
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


@router.get("/me", response_model=MeResponse)
async def me(
    db: DbDep,
    current_user: Annotated[CurrentUser | None, Depends(get_current_user)],
) -> MeResponse:
    """回傳目前登入者資訊，供登入頁／前端頂欄使用。

    `AUTH_ENABLED=false` 時 `get_current_user` 恆回傳 `None`（見
    `app/api/deps.py`），視為匿名模式而非未登入，回 200 `{anonymous: true}`；
    `AUTH_ENABLED=true` 時未帶合法 token 已由 `get_current_user` 拋 401。
    """
    if current_user is None:
        return MeResponse(anonymous=True)
    user = await users_repo.get_user_by_id(db, current_user.id)
    if user is None:
        raise HTTPException(status_code=401, detail="使用者不存在")
    return MeResponse(
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        auth_source=user.auth_source,
    )


@router.get("/sso")
async def sso(request: Request, db: DbDep) -> RedirectResponse:
    """IIS Windows SSO 自動登入端點；`AD_SSO_ENABLED=false`（預設）時回 404。

    **安全警告（`ad_sso_remote_user_header` 模式）**：本端點會無條件信任
    `ad_sso_remote_user_header` 指定的 HTTP header 所宣稱的使用者身分
    （等同 REMOTE_USER 免密碼登入）。此 header **只能**由 IIS（搭配 IIS
    Windows Authentication，經 HttpPlatformHandler 反向代理到本平台時）
    注入或覆寫；平台**必須**只透過 IIS 對外服務，絕不能讓使用者的請求
    繞過 IIS 直接打到 uvicorn，否則任何人都能偽造此 header 冒充他人登入。
    """
    settings = get_settings()
    if not settings.ad_sso_enabled:
        raise HTTPException(status_code=404)

    if settings.ad_sso_remote_user_header:
        return await _sso_remote_user_header(request, db, settings)
    return await _sso_windows_auth_token(request, settings)


async def _sso_remote_user_header(
    request: Request, db: AsyncSession, settings: Settings
) -> RedirectResponse:
    """信任 IIS 注入的 REMOTE_USER header（見 `sso()` 的安全警告）。"""
    raw_user = request.headers.get(settings.ad_sso_remote_user_header)
    if not raw_user:
        raise HTTPException(
            status_code=401, detail=f"缺少 {settings.ad_sso_remote_user_header} header"
        )

    ad_user = ad_auth.lookup_user(raw_user)
    if ad_user is not None:
        role = "admin" if ad_auth.is_admin(ad_user.member_of) else "user"
        email = ad_user.mail or ad_user.upn or ad_auth.normalize_username_to_email(raw_user)
        display_name = ad_user.display_name
    else:
        # 服務／匿名查詢失敗：仍信任 header 宣稱的身分，僅發基本 user 角色。
        role = "user"
        email = ad_auth.normalize_username_to_email(raw_user)
        display_name = None

    user = await users_repo.upsert_ad_user(db, email=email, display_name=display_name, role=role)
    result = await auth_service.issue_tokens(db, user)

    redirect = RedirectResponse(url="/", status_code=302)
    _set_auth_cookies(redirect, access_token=result.access_token, expires_in=result.expires_in)
    _set_refresh_cookie(redirect, refresh_token=result.refresh_token)
    await activity_repo.log_activity(
        db, "auth.login", {"user_id": str(user.id), "ip": _client_ip(request), "source": "ad_sso"}
    )
    return redirect


async def _sso_windows_auth_token(request: Request, settings: Settings) -> RedirectResponse:
    """`X-IIS-WindowsAuthToken`（或設定值 `ad_sso_header`）模式。

    此模式需以 pywin32 在 Windows 環境解碼 IIS HttpPlatformHandler 傳入的
    Windows access token 參照；非 Windows 環境（本開發/測試環境為 Linux）
    一律回 501，並提示改用 `ad_sso_remote_user_header` 模式。
    """
    import sys

    if sys.platform != "win32":
        raise HTTPException(
            status_code=501,
            detail=(
                f"{settings.ad_sso_header} token 模式僅支援 Windows（需 pywin32）解碼，"
                "本環境非 Windows，請改用 AD_SSO_REMOTE_USER_HEADER 模式"
                "（IIS Windows Authentication + HttpPlatformHandler 注入 REMOTE_USER）。"
            ),
        )

    token = request.headers.get(settings.ad_sso_header)
    if not token:
        raise HTTPException(status_code=401, detail=f"缺少 {settings.ad_sso_header} header")

    try:
        import win32security  # noqa: F401 -- 懶載入：僅 Windows 環境會執行到此
    except ImportError as exc:
        raise HTTPException(
            status_code=501,
            detail="pywin32 未安裝，無法解碼 Windows token，請安裝 pywin32 或改用"
            " remote-user-header 模式",
        ) from exc

    # Windows token 的實際解碼（開啟 access token handle 取得使用者 SID／群組）
    # 需在真實 Windows/IIS 部署環境驗證，本任務的開發/測試環境為 Linux 無法驗證，
    # 因此暫不實作，先回 501 並提示改用 remote-user-header 模式。
    raise HTTPException(
        status_code=501,
        detail="Windows token 解碼尚未完整實作（待 Windows 部署環境驗證後補完）。",
    )
