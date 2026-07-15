"""AD（Active Directory）登入驗證：ldap3 SIMPLE bind + 使用者屬性查詢 + SSO 身分解析。

實作依公司實戰驗證的 python-iis-ad-deploy skill（references/ad-auth.md）：

- SIMPLE bind 用 `NETBIOS\\samaccount` 格式（NetBIOS 從 `AD_SERVER` 短名導出，
  如 `ldap://KH` → `KH\\K11879`——實際驗證成功的格式）。**絕不用 NTLM bind**：
  Python 3.9+ / OpenSSL 3.0 停用 MD4，NTLM bind 會直接炸掉；SIMPLE bind 繞過
  MD4，是內網環境的標準做法。
- 群組/使用者資訊查詢：手動登入用「使用者自己 bind 成功的連線」查；SSO 路徑
  （無密碼）需要服務帳號（`AD_BIND_DN`/`AD_BIND_PW`），沒有服務帳號就不查群組
  （只發基本 user 角色），**不做匿名查詢**。
- SSO 的 NTLM Type-3 解碼（純 Python）與 Windows token handle 解碼（ctypes，
  僅 Windows）皆照 skill 原文實作。

只在 `ad_enabled=true`（或 SSO 情境）時被呼叫。`ldap3` 為選用依賴，一律在
函式內才 `import`——`ad_enabled=false` 時模組可被安全 import，不需要
`ldap3` 已安裝，也不會產生任何額外行為。
"""

import base64
import re
import struct
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

    username: str  # 使用者輸入（或 SSO 解析出）的原始帳號
    sam_account_name: str
    display_name: str | None
    mail: str | None
    member_of: list[str]  # memberOf 完整 DN 清單


# -- 帳號格式輔助 -------------------------------------------------------------------


def _netbios() -> str:
    """從 `AD_SERVER` 短名導出 NetBIOS 網域名：`ldap://KH` → `KH`。"""
    settings = get_settings()
    server = settings.ad_server or ""
    return server.replace("ldap://", "").replace("ldaps://", "").split("/")[0].upper()


def sam_from_identifier(identifier: str) -> str:
    """由 `DOMAIN\\user`／UPN／裸帳號抽出 sAMAccountName（取 `\\` 後、`@` 前段）。"""
    return identifier.split("\\")[-1].split("@")[0]


def default_email(samaccount: str) -> str:
    """AD 條目沒有 mail 屬性時的保底 email：`sam@AD_DOMAIN`（未設定則原樣回傳）。"""
    settings = get_settings()
    return f"{samaccount}@{settings.ad_domain}" if settings.ad_domain else samaccount


def _mock_user(samaccount: str) -> ADUser:
    """`AD_MOCK=true`（僅供開發）的固定測試身分。"""
    return ADUser(
        username=samaccount,
        sam_account_name=samaccount,
        display_name=f"{samaccount}（AD_MOCK 測試身分）",
        mail=default_email(samaccount),
        member_of=[],
    )


# -- LDAP 查詢輔助 -----------------------------------------------------------------


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


def _fetch_user(conn, original_username: str, samaccount: str) -> ADUser:
    """用已 bind 的連線查詢使用者條目，補齊 displayName／mail／memberOf。"""
    import ldap3

    settings = get_settings()
    conn.search(
        settings.ad_base_dn or "",
        f"(sAMAccountName={_escape_filter_value(samaccount)})",
        search_scope=ldap3.SUBTREE,
        attributes=["sAMAccountName", "displayName", "mail", "memberOf"],
    )
    entries = list(getattr(conn, "entries", None) or [])
    if not entries:
        # 查無條目（如 AD_BASE_DN 未設定/設錯）：仍視為驗證成功，只是屬性補不齊。
        return ADUser(
            username=original_username,
            sam_account_name=samaccount,
            display_name=None,
            mail=None,
            member_of=[],
        )
    entry = entries[0]
    return ADUser(
        username=original_username,
        sam_account_name=_entry_single(entry, "sAMAccountName") or samaccount,
        display_name=_entry_single(entry, "displayName"),
        mail=_entry_single(entry, "mail"),
        member_of=_entry_multi(entry, "memberOf"),
    )


# -- 手動登入（帳密驗證） ------------------------------------------------------------


def authenticate(username: str, password: str) -> ADUser | None:
    """以 SIMPLE bind 驗證帳密（bind 格式 `NETBIOS\\samaccount`）。

    - 帳密正確：回傳 `ADUser`（用使用者自己 bind 成功的連線補齊
      displayName／mail／memberOf）。
    - 帳密錯誤（bind 失敗）：回傳 `None`。
    - 連線／伺服器層級錯誤（DNS、網路、TLS…）：raise `ADConnectionError`。
    - `AD_MOCK=true`（**僅供開發**）：不連 AD，任何非空密碼皆放行，回固定測試身分。
    """
    settings = get_settings()
    samaccount = sam_from_identifier(username)
    if settings.ad_mock:
        return _mock_user(samaccount) if password else None
    if not settings.ad_server:
        raise ADConnectionError("AD_SERVER 未設定，無法進行 AD 登入")
    if not password:
        return None

    import ldap3
    from ldap3.core.exceptions import LDAPException, LDAPSocketOpenError

    # get_info=NONE：不拉 schema，速度快（skill 實測建議）。
    server = ldap3.Server(settings.ad_server, get_info=ldap3.NONE)
    user_str = f"{_netbios()}\\{samaccount}"  # 例：KH\K11879（實際驗證成功的格式）
    conn = ldap3.Connection(server, user=user_str, password=password, authentication=ldap3.SIMPLE)
    try:
        bound = conn.bind()
    except LDAPSocketOpenError as exc:
        raise ADConnectionError(f"無法連線至 AD 伺服器 {settings.ad_server}：{exc}") from exc
    except LDAPException:
        # bind 層級失敗（帳密錯誤等），視為驗證失敗
        return None
    if not bound:
        return None
    try:
        return _fetch_user(conn, username, samaccount)
    finally:
        conn.unbind()


# -- SSO 路徑的使用者資訊查詢（需要服務帳號） -----------------------------------------


def get_user_info(samaccount: str) -> ADUser | None:
    """SSO 情境專用：以服務帳號（`AD_BIND_DN`/`AD_BIND_PW`）查使用者屬性。

    skill 明載：**沒有服務帳號就不查群組**（不做匿名查詢），此時回傳 `None`，
    呼叫端據此只發基本 user 角色，不阻斷 SSO 流程。查詢過程任何失敗
    （bind 被拒、查無使用者、連線異常…）也一律回傳 `None`。
    """
    settings = get_settings()
    if settings.ad_mock:
        return _mock_user(samaccount)
    if not (settings.ad_bind_dn and settings.ad_bind_pw and settings.ad_server):
        return None
    try:
        import ldap3

        # 服務帳號已是 DOMAIN\user／UPN／DN 格式就直接用，否則補 NETBIOS\ 前綴。
        bind_dn = settings.ad_bind_dn
        if "\\" in bind_dn or "@" in bind_dn or "," in bind_dn:
            bind_user = bind_dn
        else:
            bind_user = f"{_netbios()}\\{bind_dn}"

        server = ldap3.Server(settings.ad_server, get_info=ldap3.NONE)
        conn = ldap3.Connection(
            server, user=bind_user, password=settings.ad_bind_pw, authentication=ldap3.SIMPLE
        )
        if not conn.bind():
            return None
        try:
            return _fetch_user(conn, samaccount, samaccount)
        finally:
            conn.unbind()
    except Exception:  # noqa: BLE001 -- SSO 補充查詢刻意全面吞例外，失敗即退回基本角色
        return None


# -- 群組授權 ----------------------------------------------------------------------


def group_cns(member_of: list[str]) -> list[str]:
    """從 memberOf 完整 DN 清單萃取群組名（DN 第一段的 `CN=xxx`）。"""
    return [_CN_PREFIX.sub("", dn.split(",")[0].strip()) for dn in member_of]


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


# -- SSO 身分解析（照 skill 原文實作） ------------------------------------------------


def parse_ntlm_username(auth_header: str) -> str:
    """NTLM Type-3 訊息的帳號在固定 offset，可直接解碼（純 Python，不需 Windows API）。

    照 skill 的 `parse_ntlm_username` 原文：base64 解碼後，offset 8 的訊息型別
    須為 3（Type-3 AUTHENTICATE），帳號長度在 offset 36（<H）、帳號位移在
    offset 40（<I），內容為 UTF-16-LE。任何解析失敗回傳空字串。
    """
    try:
        data = base64.b64decode(auth_header.split(" ", 1)[-1].strip())
        if len(data) < 44 or struct.unpack_from("<I", data, 8)[0] != 3:
            return ""
        un_len = struct.unpack_from("<H", data, 36)[0]
        un_offset = struct.unpack_from("<I", data, 40)[0]
        return data[un_offset : un_offset + un_len].decode("utf-16-le")
    except Exception:  # noqa: BLE001 -- 解析失敗一律回空字串（skill 原文行為）
        return ""


def username_from_token(token_handle: int) -> str:
    """從 IIS 傳來的 Windows token handle 解析帳號名稱（僅 Windows，ctypes 實作）。

    照 skill 的 `username_from_token` 原文：直接讀 token 的 SID
    （GetTokenInformation + LookupAccountSidW），不靠 impersonation。
    呼叫端須先確認 `sys.platform == 'win32'`；任何失敗回傳空字串。
    """
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.windll.advapi32

    # 明確宣告型別，避免 64-bit handle 被截成 32-bit
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.LookupAccountSidW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.LookupAccountSidW.restype = wintypes.BOOL

    token_user = 1  # TokenUser
    size = wintypes.DWORD(0)
    advapi32.GetTokenInformation(token_handle, token_user, None, 0, ctypes.byref(size))
    if size.value == 0:
        return ""

    buf = ctypes.create_string_buffer(size.value)
    if not advapi32.GetTokenInformation(token_handle, token_user, buf, size, ctypes.byref(size)):
        return ""

    psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]

    name = ctypes.create_unicode_buffer(256)
    name_len = wintypes.DWORD(256)
    dom = ctypes.create_unicode_buffer(256)
    dom_len = wintypes.DWORD(256)
    sid_type = wintypes.DWORD()

    if not advapi32.LookupAccountSidW(
        None, psid, name, ctypes.byref(name_len), dom, ctypes.byref(dom_len), ctypes.byref(sid_type)
    ):
        return ""

    return name.value  # 純帳號名，e.g. "K11879"
