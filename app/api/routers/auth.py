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

import sys
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.schemas.auth import MeResponse
from app.config import Settings, get_settings
from app.repos import activity as activity_repo
from app.repos import users as users_repo
from app.services import ad_auth, auth_service

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
async def me(request: Request, db: DbDep) -> MeResponse:
    """回傳目前登入者資訊，供登入頁／前端頂欄使用。

    **永遠回 200、不回 401**（python-iis-ad-deploy skill 明載：IIS Windows
    Auth 下 401 會觸發瀏覽器原生憑證彈窗）——未登入／token 無效／
    `AUTH_ENABLED=false` 一律回 `{anonymous: true, auth_type: null}`，
    由前端自行顯示登入表單。
    """
    if not get_settings().auth_enabled:
        return MeResponse(anonymous=True)

    token = None
    authorization = request.headers.get("Authorization")
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[len("bearer ") :].strip()
    elif request.cookies.get("access_token"):
        token = request.cookies["access_token"]
    if not token:
        return MeResponse(anonymous=True)

    try:
        current_user = auth_service.decode_access_token(token)
    except auth_service.AuthError:
        return MeResponse(anonymous=True)
    user = await users_repo.get_user_by_id(db, current_user.id)
    if user is None:
        return MeResponse(anonymous=True)

    # 舊 token 沒有 auth_type claim（或 refresh 換發時未保留）：依 auth_source 推導保底值。
    auth_type = current_user.auth_type or (
        "local" if user.auth_source == "local" else "manual"
    )
    return MeResponse(
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        auth_source=user.auth_source,
        auth_type=auth_type,
    )


@router.get("/sso")
async def sso(request: Request, db: DbDep) -> RedirectResponse:
    """IIS Windows SSO 自動登入端點；`AD_SSO_ENABLED=false`（預設）時回 404。

    身分解析照 skill 的 `get_sso_username` 三層順序（見 `_resolve_sso_username`）：
    REMOTE_USER 式 header → NTLM/Negotiate Authorization header 純 Python 解碼
    → Windows token handle（ctypes，僅 Windows）。解析出帳號後 JIT 供裝
    （有服務帳號才查群組，否則只發基本 user 角色）並簽發 JWT、302 到 `/`。

    **安全警告（`ad_sso_remote_user_header` 模式）**：本端點會無條件信任
    `ad_sso_remote_user_header` 指定的 HTTP header 所宣稱的使用者身分
    （等同 REMOTE_USER 免密碼登入）。此 header **只能**由 IIS（搭配 IIS
    Windows Authentication，經 HttpPlatformHandler 反向代理到本平台時）
    注入或覆寫；平台**必須**只透過 IIS 對外服務，絕不能讓使用者的請求
    繞過 IIS 直接打到 uvicorn，否則任何人都能偽造此 header 冒充他人登入。

    與 skill 的差異：skill 是「每請求自動 SSO + session 的 logged_out 壓制
    旗標」；本平台改為顯式的 `/auth/sso` 進入點 + JWT cookie，登出（
    `POST /auth/logout`）清 cookie 即可，之後不會被自動重新登入，因此不需要
    logged_out 壓制旗標。
    """
    settings = get_settings()
    if not settings.ad_sso_enabled:
        raise HTTPException(status_code=404)

    samaccount = _resolve_sso_username(request, settings)
    if not samaccount:
        # 解析不到身分也不回 401（避免 IIS 觸發瀏覽器憑證彈窗）：
        # 導回首頁、不帶 cookie，由前端顯示登入表單。
        return RedirectResponse(url="/", status_code=302)

    # 有服務帳號（AD_BIND_DN/PW）才查群組/顯示名；沒有就只發基本 user 角色
    # （skill 明載：沒服務帳號就不查群組，不做匿名查詢）。
    ad_user = ad_auth.get_user_info(samaccount)
    if ad_user is not None:
        role = "admin" if ad_auth.is_admin(ad_user.member_of) else "user"
        email = ad_user.mail or ad_auth.default_email(samaccount)
        display_name = ad_user.display_name
    else:
        role = "user"
        email = ad_auth.default_email(samaccount)
        display_name = None

    user = await users_repo.upsert_ad_user(db, email=email, display_name=display_name, role=role)
    result = await auth_service.issue_tokens(db, user, auth_type="sso")

    redirect = RedirectResponse(url="/", status_code=302)
    _set_auth_cookies(redirect, access_token=result.access_token, expires_in=result.expires_in)
    _set_refresh_cookie(redirect, refresh_token=result.refresh_token)
    await activity_repo.log_activity(
        db, "auth.login", {"user_id": str(user.id), "ip": _client_ip(request), "source": "ad_sso"}
    )
    return redirect


def _resolve_sso_username(request: Request, settings: Settings) -> str:
    """SSO 身分解析，照 skill `get_sso_username` 的三層順序；回傳 sAMAccountName
    （解析不到回空字串）。

    1. REMOTE_USER 式 header（`ad_sso_remote_user_header` 有設定時）：
       取 `\\` 後、`@` 前段。
    2. `Authorization: NTLM/Negotiate` header：NTLM Type-3 訊息純 Python 解碼
       （見 `ad_auth.parse_ntlm_username`）。
    3. `X-IIS-WindowsAuthToken`（`ad_sso_header`）的 Windows token handle：
       ctypes 解碼（見 `ad_auth.username_from_token`），僅 Windows；
       非 Windows 環境回 501。
    """
    # 方法 1：REMOTE_USER 式 header（ARR 反向代理環境有時有）
    if settings.ad_sso_remote_user_header:
        raw = request.headers.get(settings.ad_sso_remote_user_header, "")
        if raw:
            return ad_auth.sam_from_identifier(raw)

    # 方法 2：從 NTLM token 直接解碼（瀏覽器走 NTLM 時）
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith(("NTLM ", "Negotiate ")):
        username = ad_auth.parse_ntlm_username(authorization)
        if username:
            return ad_auth.sam_from_identifier(username)

    # 方法 3：Windows token handle（HttpPlatformHandler forwardWindowsAuthToken 主要方式）
    token_str = request.headers.get(settings.ad_sso_header, "")
    if token_str:
        if sys.platform != "win32":
            raise HTTPException(
                status_code=501,
                detail=(
                    f"{settings.ad_sso_header} token 模式需在 Windows 上以 ctypes 解碼"
                    "（GetTokenInformation + LookupAccountSidW），本環境非 Windows；"
                    "請改用 AD_SSO_REMOTE_USER_HEADER 模式。"
                ),
            )
        return ad_auth.username_from_token(int(token_str, 16))

    return ""
