"""AD（Active Directory）登入驗證：ldap3 SIMPLE bind + 使用者屬性查詢。

只在 `ad_enabled=true`（或 SSO 情境）時被呼叫。`ldap3` 為選用依賴，一律在
函式內才 `import`——`ad_enabled=false` 時模組可被安全 import，不需要
`ldap3` 已安裝，也不會產生任何額外行為。
"""

import re
from dataclasses import dataclass

from app.config import get_settings


class ADConnectionError(Exception):
    """AD 伺服器連線層級的錯誤（DNS／網路／TLS 等），非帳號密碼錯誤。

    呼叫端不應把這類錯誤當成「帳密錯誤」去 fallback 本地帳號，而應視為
    系統性錯誤上拋（見 app/services/auth_service.py 的 `ADUnavailableError`）。
    """


@dataclass
class ADUser:
    """AD bind／查詢成功後取得的使用者屬性。"""

    username: str  # 使用者輸入的原始帳號
    upn: str | None
    sam_account_name: str | None
    display_name: str | None
    mail: str | None
    member_of: list[str]


# -- bind 身分格式 ---------------------------------------------------------------


def _candidate_binds(username: str) -> list[str]:
    """組出待嘗試的 bind 身分字串。

    使用者輸入已含 `@`（UPN）或 `\\`（DOMAIN\\user）格式時直接使用；
    否則依序嘗試 UPN 格式（`ad_upn_suffix`）、DOMAIN\\user 格式（`ad_domain`）。
    """
    if "@" in username or "\\" in username:
        return [username]
    settings = get_settings()
    candidates = []
    if settings.ad_upn_suffix:
        candidates.append(f"{username}@{settings.ad_upn_suffix}")
    if settings.ad_domain:
        candidates.append(f"{settings.ad_domain}\\{username}")
    if not candidates:
        candidates.append(username)
    return candidates


def normalize_username_to_email(raw: str) -> str:
    """SSO header 情境的保底 email 組法（AD 查詢失敗時使用）。

    已是 UPN（含 `@`）直接沿用；`DOMAIN\\user` 格式接上 `ad_upn_suffix`
    （未設定則退回 `user@DOMAIN`）；純帳號則接上 `ad_upn_suffix`（未設定則原樣回傳）。
    """
    settings = get_settings()
    if "@" in raw:
        return raw
    if "\\" in raw:
        domain, sam = raw.split("\\", 1)
        return f"{sam}@{settings.ad_upn_suffix}" if settings.ad_upn_suffix else f"{sam}@{domain}"
    return f"{raw}@{settings.ad_upn_suffix}" if settings.ad_upn_suffix else raw


# -- LDAP 查詢輔助 -----------------------------------------------------------------


def _sam_from_identifier(identifier: str) -> str:
    """由 `DOMAIN\\user`／UPN／裸帳號抽出 sAMAccountName 猜測值，供查詢使用。"""
    return identifier.split("\\")[-1].split("@")[0]


def _escape_filter_value(value: str) -> str:
    """LDAP filter 特殊字元跳脫（RFC 4515），避免 filter injection。"""
    for char, escaped in (
        ("\\", "\\5c"),
        ("*", "\\2a"),
        ("(", "\\28"),
        (")", "\\29"),
        ("\x00", "\\00"),
    ):
        value = value.replace(char, escaped)
    return value


def _entry_multi(entry, name: str) -> list[str]:
    attr = getattr(entry, name, None)
    values = getattr(attr, "values", None) if attr is not None else None
    return list(values) if values else []


def _entry_single(entry, name: str) -> str | None:
    values = _entry_multi(entry, name)
    return values[0] if values else None


def _fetch_user(conn, original_username: str, bind_identifier: str) -> ADUser:
    """用已 bind 的連線查詢使用者條目，補齊 displayName／mail／memberOf。"""
    settings = get_settings()
    sam = _sam_from_identifier(bind_identifier)
    conn.search(
        search_base=settings.ad_search_base or "",
        search_filter=f"(sAMAccountName={_escape_filter_value(sam)})",
        attributes=["displayName", "mail", "memberOf", "userPrincipalName", "sAMAccountName"],
    )
    entries = list(getattr(conn, "entries", None) or [])
    fallback_upn = bind_identifier if "@" in bind_identifier else None
    if not entries:
        return ADUser(
            username=original_username,
            upn=fallback_upn,
            sam_account_name=sam,
            display_name=None,
            mail=None,
            member_of=[],
        )
    entry = entries[0]
    return ADUser(
        username=original_username,
        upn=_entry_single(entry, "userPrincipalName") or fallback_upn,
        sam_account_name=_entry_single(entry, "sAMAccountName") or sam,
        display_name=_entry_single(entry, "displayName"),
        mail=_entry_single(entry, "mail"),
        member_of=_entry_multi(entry, "memberOf"),
    )


# -- 對外 API ---------------------------------------------------------------------


def authenticate(username: str, password: str) -> ADUser | None:
    """以 SIMPLE bind 驗證帳密。

    - 帳密正確：回傳 `ADUser`（已補齊 displayName／mail／memberOf）。
    - 帳密錯誤（所有候選 bind 格式皆失敗）：回傳 `None`。
    - 連線／伺服器層級錯誤（DNS、網路、TLS…）：raise `ADConnectionError`。
    """
    settings = get_settings()
    if not settings.ad_server:
        raise ADConnectionError("AD_SERVER 未設定，無法進行 AD 登入")
    if not password:
        return None

    import ldap3
    from ldap3.core.exceptions import LDAPException, LDAPSocketOpenError

    server = ldap3.Server(settings.ad_server, use_ssl=settings.ad_use_ssl, get_info=ldap3.NONE)

    for bind_identifier in _candidate_binds(username):
        conn = ldap3.Connection(server, user=bind_identifier, password=password)
        try:
            bound = conn.bind()
        except LDAPSocketOpenError as exc:
            raise ADConnectionError(f"無法連線至 AD 伺服器 {settings.ad_server}：{exc}") from exc
        except LDAPException:
            # 帳密錯誤等 bind 層級失敗，嘗試下一個候選格式
            continue
        if not bound:
            continue
        try:
            return _fetch_user(conn, username, bind_identifier)
        finally:
            conn.unbind()
    return None


def lookup_user(username: str) -> ADUser | None:
    """SSO 情境專用：不需密碼，以匿名 bind 查詢使用者屬性（補群組／顯示名）。

    任何失敗（未設定 `ad_server`、匿名 bind 被拒、查無使用者、連線異常…）
    一律回傳 `None`，呼叫端據此退回只發基本 user 角色，不阻斷 SSO 流程。
    """
    settings = get_settings()
    if not settings.ad_server:
        return None
    try:
        import ldap3

        server = ldap3.Server(settings.ad_server, use_ssl=settings.ad_use_ssl, get_info=ldap3.NONE)
        conn = ldap3.Connection(server)
        if not conn.bind():
            return None
        try:
            return _fetch_user(conn, username, username)
        finally:
            conn.unbind()
    except Exception:  # noqa: BLE001 -- SSO 補充查詢刻意全面吞例外，失敗即退回基本角色
        return None


_CN_PREFIX = re.compile(r"^cn=", re.IGNORECASE)


def is_admin(member_of: list[str]) -> bool:
    """比對 `ad_admin_group`（支援群組 CN 名或完整 DN，大小寫不敏感）。"""
    settings = get_settings()
    target = settings.ad_admin_group
    if not target:
        return False
    target_lower = target.strip().lower()
    for dn in member_of:
        dn_lower = dn.strip().lower()
        if dn_lower == target_lower:
            return True
        first_component = dn_lower.split(",")[0].strip()
        if _CN_PREFIX.match(first_component) and first_component[3:] == target_lower:
            return True
    return False
