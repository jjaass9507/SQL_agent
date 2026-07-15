"""認證服務：密碼雜湊、JWT 簽發/驗證、登入/refresh/登出流程、簡易 rate limiting。

依 `docs/security_design.md` 第二章實作：HS256 JWT、access 15 分/refresh 7 天、
refresh token 存 DB（雜湊後）支援登出撤銷。`AUTH_ENABLED=false` 時本模組只會被
`app/api/routers/auth.py` 呼叫（新端點），既有端點的行為完全不受影響。
"""

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.repos import users as users_repo
from app.repos.models import User
from app.services import ad_auth

JWT_ALGORITHM = "HS256"

# scrypt 參數：N（成本因子）/ r（區塊大小）/ p（平行度），依 stdlib hashlib.scrypt 建議值。
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


class AuthError(Exception):
    """登入 / refresh / 登出失敗的共同例外，router 轉換成對應的 HTTP status。"""


class ADUnavailableError(AuthError):
    """AD 伺服器暫時無法連線（DNS／網路／TLS 等），非帳密錯誤。

    刻意繼承 `AuthError`——舊有 `except AuthError` 呼叫端仍能捕捉到，
    router 再用 `isinstance` 細分成 503（而非帳密錯誤慣用的 401）。
    """


@dataclass
class CurrentUser:
    """access token 驗證通過後的使用者資訊（直接信任 payload，不查 DB）。

    `auth_type` 為登入方式（`manual`＝AD 手動登入、`sso`＝IIS Windows SSO、
    `local`＝本地帳密）；舊 token 沒有此 claim 時為 None，由 `/auth/me`
    依 `auth_source` 推導保底值。
    """

    id: UUID
    role: str
    auth_type: str | None = None


# -- 密碼雜湊（stdlib hashlib.scrypt，避免新增依賴） ---------------------------


def hash_password(password: str) -> str:
    """回傳格式 `scrypt$N$r$p$salt_hex$hash_hex`。"""
    salt = os.urandom(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """驗證密碼是否與雜湊值相符（timing-safe 比對）；格式不符一律回傳 False。"""
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored_hash.split("$")
        if scheme != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(hash_hex) // 2,
        )
        return hmac.compare_digest(derived.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


# -- JWT -----------------------------------------------------------------------


def create_access_token(user: User, auth_type: str | None = None) -> tuple[str, int]:
    """簽發 access token，回傳 `(token, 存活秒數)`。payload 依 security_design.md：
    `{sub, role, iat, exp}`；`auth_type` 有值時額外附上（manual/sso/local）。"""
    settings = get_settings()
    ttl = timedelta(minutes=settings.jwt_access_ttl_minutes)
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    if auth_type:
        payload["auth_type"] = auth_type
    token = jwt.encode(payload, settings.secret_key, algorithm=JWT_ALGORITHM)
    return token, int(ttl.total_seconds())


def decode_access_token(token: str) -> CurrentUser:
    """驗證並解析 access token；簽章不符、過期、格式錯誤一律拋 `AuthError`。"""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise AuthError("access token 無效或已過期") from exc
    try:
        return CurrentUser(
            id=UUID(payload["sub"]), role=payload["role"], auth_type=payload.get("auth_type")
        )
    except (KeyError, ValueError) as exc:
        raise AuthError("access token payload 格式錯誤") from exc


def _hash_refresh_token(raw_token: str) -> str:
    """refresh token 存 DB 前先雜湊（SHA-256），資料庫外洩不會直接洩漏可用憑證。"""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


async def _issue_refresh_token(db: AsyncSession, user: User) -> str:
    settings = get_settings()
    raw_token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_ttl_days)
    await users_repo.create_refresh_token(
        db, user_id=user.id, token_hash=_hash_refresh_token(raw_token), expires_at=expires_at
    )
    return raw_token


# -- 登入 / refresh / 登出 -------------------------------------------------------


@dataclass
class LoginResult:
    access_token: str
    refresh_token: str
    expires_in: int
    user: User


async def issue_tokens(
    db: AsyncSession, user: User, auth_type: str | None = None
) -> LoginResult:
    """簽發 access + refresh token 組合；登入（本地／AD）與 SSO 共用。

    `auth_type`（manual/sso/local）會寫進 access token claim，供 `/auth/me`
    回報登入方式。"""
    access_token, expires_in = create_access_token(user, auth_type=auth_type)
    refresh_token = await _issue_refresh_token(db, user)
    return LoginResult(
        access_token=access_token, refresh_token=refresh_token, expires_in=expires_in, user=user
    )


async def login(db: AsyncSession, email: str, password: str) -> LoginResult:
    """本地帳密驗證；失敗一律拋 `AuthError`（訊息不透露帳號是否存在）。

    AD 使用者（`password_hash` 為 NULL，見 `app/repos/users.py` 的
    `upsert_ad_user`）一律視為本地登入失敗——AD 帳密不落地，只能經
    `login_with_credentials` 的 AD 分支驗證。
    """
    user = await users_repo.get_user_by_email(db, email)
    if user is None or user.password_hash is None or not verify_password(
        password, user.password_hash
    ):
        raise AuthError("帳號或密碼錯誤")
    return await issue_tokens(db, user, auth_type="local")


async def provision_ad_user(db: AsyncSession, ad_user: ad_auth.ADUser) -> User:
    """AD 驗證/查詢成功後的 JIT 供裝：email 取 mail 屬性，缺少時組 `sam@AD_DOMAIN`；
    role 依 `ad_admin_group` 群組比對，每次登入刷新。"""
    role = "admin" if ad_auth.is_admin(ad_user.member_of) else "user"
    email = ad_user.mail or ad_auth.default_email(ad_user.sam_account_name)
    return await users_repo.upsert_ad_user(
        db, email=email, display_name=ad_user.display_name, role=role
    )


async def login_with_credentials(
    db: AsyncSession, username: str, password: str
) -> tuple[LoginResult, str]:
    """整合 AD + 本地登入（`POST /auth/login` 使用）。

    回傳 `(LoginResult, source)`，`source` 為 `"ad"` 或 `"local"`，供 router
    寫入 audit log。`ad_enabled=false` 時行為與純本地登入完全相同。

    流程：`ad_enabled=true` 時先試 AD SIMPLE bind（`NETBIOS\\samaccount`
    格式）；bind 因帳密錯誤失敗（非連線錯誤）時 fallback 本地 email+密碼；
    AD 連線層級錯誤則 raise `ADUnavailableError`，不嘗試 fallback
    （避免把系統性故障誤判成帳密錯誤）。
    """
    settings = get_settings()
    if settings.ad_enabled:
        try:
            ad_user = ad_auth.authenticate(username, password)
        except ad_auth.ADConnectionError as exc:
            raise ADUnavailableError(str(exc)) from exc
        if ad_user is not None:
            user = await provision_ad_user(db, ad_user)
            return await issue_tokens(db, user, auth_type="manual"), "ad"
        # AD bind 失敗（帳密錯誤，非連線問題）→ fallback 本地帳密

    result = await login(db, username, password)
    return result, "local"


@dataclass
class RefreshResult:
    access_token: str
    expires_in: int


async def refresh_access_token(db: AsyncSession, refresh_token: str) -> RefreshResult:
    """憑有效且未撤銷的 refresh token 換發新 access token。"""
    record = await users_repo.get_refresh_token_by_hash(db, _hash_refresh_token(refresh_token))
    if record is None or record.revoked_at is not None:
        raise AuthError("refresh token 無效或已撤銷")
    # SQLite 的 DateTime(timezone=True) 讀回來是 naive（一律視為 UTC），補上 tzinfo 再比較。
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise AuthError("refresh token 已過期")
    user = await users_repo.get_user_by_id(db, record.user_id)
    if user is None:
        raise AuthError("使用者不存在")
    access_token, expires_in = create_access_token(user)
    return RefreshResult(access_token=access_token, expires_in=expires_in)


async def logout(db: AsyncSession, refresh_token: str) -> None:
    """撤銷 refresh token；找不到也視為成功（登出操作維持冪等）。"""
    await users_repo.revoke_refresh_token(db, _hash_refresh_token(refresh_token))


# -- Rate limiting（簡單 in-memory 滑動視窗，per-IP） ------------------------------


class RateLimiter:
    """單一 process 的 in-memory 滑動視窗限流器。

    多副本部署時每個副本各自計數，不是全域精確限流——足以擋掉單一來源的暴力
    嘗試，嚴謹的跨副本限流需要外部儲存（Redis 等），非本階段範圍。
    """

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}

    def check(self, key: str, *, max_requests: int, window_seconds: float) -> bool:
        """回傳本次請求是否允許放行；放行時一併記錄本次命中。"""
        now = time.monotonic()
        window_start = now - window_seconds
        hits = [t for t in self._hits.get(key, []) if t > window_start]
        if len(hits) >= max_requests:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True


# 模組層級單例：/auth/login 專用（較嚴格門檻），見 app/api/routers/auth.py。
login_rate_limiter = RateLimiter()
