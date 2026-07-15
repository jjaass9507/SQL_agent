# AD 認證整合參考

## 身分優先順序

```
session["manual_user"]   ← 手動登入（最高優先，覆蓋 SSO）
        ↓ 沒有
session["logged_out"]    ← True 時壓制 SSO，等待手動登入
        ↓ 沒有
SSO（Windows token 解析）← 自動，不需密碼
        ↓ 也沒有
無身分 → 回傳 200 + {auth_type: null}（不回 401！）
```

> **永遠不回 401**：IIS 看到 401 會彈出原生 Windows 憑證視窗，
> 用 200 + `auth_type: null` 讓前端自行顯示登入表單。

---

## SSO 身分解析（自動，無需密碼）

### 正確做法：GetTokenInformation + LookupAccountSid

```python
import ctypes
from ctypes import wintypes

def username_from_token(token_handle: int) -> str:
    """
    從 IIS 傳來的 Windows token handle 解析帳號名稱。
    直接讀 token 的 SID，不靠 impersonation。
    """
    advapi32 = ctypes.windll.advapi32

    # 明確宣告型別，避免 64-bit handle 被截成 32-bit
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.LookupAccountSidW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_void_p,
        wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD)]
    advapi32.LookupAccountSidW.restype = wintypes.BOOL

    TokenUser = 1
    size = wintypes.DWORD(0)
    advapi32.GetTokenInformation(token_handle, TokenUser, None, 0, ctypes.byref(size))
    if size.value == 0:
        return ""

    buf = ctypes.create_string_buffer(size.value)
    if not advapi32.GetTokenInformation(token_handle, TokenUser, buf, size, ctypes.byref(size)):
        return ""

    psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]

    name     = ctypes.create_unicode_buffer(256)
    name_len = wintypes.DWORD(256)
    dom      = ctypes.create_unicode_buffer(256)
    dom_len  = wintypes.DWORD(256)
    sid_type = wintypes.DWORD()

    if not advapi32.LookupAccountSidW(
        None, psid, name, ctypes.byref(name_len),
        dom, ctypes.byref(dom_len), ctypes.byref(sid_type)
    ):
        return ""

    return name.value   # 純帳號名，e.g. "K11879"
```

### 身分取得流程

```python
def get_sso_username() -> str:
    # 方法 1：REMOTE_USER（ARR 反向代理環境有時有）
    remote = request.environ.get("REMOTE_USER", "")
    if remote:
        return remote.split("\\")[-1].split("@")[0]

    # 方法 2：從 NTLM token 直接解碼（瀏覽器走 NTLM 時）
    auth = request.environ.get("HTTP_AUTHORIZATION", "")
    if auth.startswith(("NTLM ", "Negotiate ")):
        username = parse_ntlm_username(auth)
        if username:
            return username

    # 方法 3：Windows token handle（HttpPlatformHandler 主要方式）
    token_str = request.environ.get("HTTP_X_IIS_WINDOWSAUTHTOKEN", "")
    if token_str:
        return username_from_token(int(token_str, 16))

    return ""
```

### NTLM token 直接解碼（純 Python，不需 Windows API）

```python
import base64, struct

def parse_ntlm_username(auth_header: str) -> str:
    """NTLM Type-3 訊息的帳號在固定 offset，可直接解碼"""
    try:
        data = base64.b64decode(auth_header.split(" ", 1)[-1].strip())
        if len(data) < 44 or struct.unpack_from("<I", data, 8)[0] != 3:
            return ""
        un_len    = struct.unpack_from("<H", data, 36)[0]
        un_offset = struct.unpack_from("<I", data, 40)[0]
        return data[un_offset: un_offset + un_len].decode("utf-16-le")
    except Exception:
        return ""
```

---

## 手動 AD 登入（切換帳號用）

### 密碼驗證：SIMPLE bind

```python
from ldap3 import Server, Connection, NONE, SIMPLE

def verify_ad_password(samaccount: str, password: str) -> bool:
    # NetBIOS 從 AD_SERVER 取得：ldap://KH → KH
    netbios  = AD_SERVER.replace("ldap://", "").replace("ldaps://", "").split("/")[0].upper()
    user_str = f"{netbios}\\{samaccount}"   # 例：KH\K11879（實際驗證成功的格式）

    try:
        server = Server(AD_SERVER, get_info=NONE)  # NONE：不拉 schema，速度快
        conn   = Connection(server, user=user_str, password=password,
                            authentication=SIMPLE, auto_bind=True)
        conn.unbind()
        return True
    except Exception:
        return False
```

> **不用 NTLM**：Python 3.9+ / OpenSSL 3.0 停用 MD4 → NTLM bind 直接炸掉。
> SIMPLE bind 繞過 MD4，是內網環境的標準做法。

---

## 登出 / 切換帳號流程

```python
def get_current_user() -> dict:
    manual = session.get("manual_user")
    if manual:
        return {"samaccount": manual, "auth_type": "manual"}
    if session.get("logged_out"):
        return {}          # 壓制 SSO，等待手動輸入
    sso = get_sso_username()
    if sso:
        return {"samaccount": sso, "auth_type": "sso"}
    return {}

@app.route("/auth/logout", methods=["POST"])
def logout():
    session.pop("manual_user", None)
    session["logged_out"] = True   # 壓制 SSO
    return jsonify({"ok": True})   # 回 200，不要回 401

@app.route("/auth/login", methods=["POST"])
def login():
    sam, pw = request.json.get("username"), request.json.get("password")
    if not verify_ad_password(sam, pw):
        return jsonify({"error": "帳號或密碼錯誤"}), 401
    session["manual_user"] = sam
    session.pop("logged_out", None)   # 清除壓制旗標
    return jsonify({"ok": True, "auth_type": "manual"})
```

---

## AD 使用者資訊查詢（需要服務帳號）

```python
from ldap3 import Server, Connection, NONE, SIMPLE, SUBTREE

def get_ad_user_info(samaccount: str) -> dict:
    if not (AD_BIND_DN and AD_BIND_PW):
        # 沒有服務帳號：只回傳帳號名稱，不查群組
        return {"samaccount": samaccount, "displayName": samaccount,
                "email": "", "department": "", "title": "", "groups": []}

    netbios   = AD_SERVER.replace("ldap://", "").upper()
    bind_user = AD_BIND_DN if ("\\" in AD_BIND_DN or "@" in AD_BIND_DN or "," in AD_BIND_DN) \
                else f"{netbios}\\{AD_BIND_DN}"

    server = Server(AD_SERVER, get_info=NONE)
    conn   = Connection(server, user=bind_user, password=AD_BIND_PW,
                        authentication=SIMPLE, auto_bind=True)
    conn.search(AD_BASE_DN, f"(sAMAccountName={samaccount})",
                search_scope=SUBTREE,
                attributes=["sAMAccountName","cn","displayName",
                            "mail","department","title","memberOf"])
    if not conn.entries:
        return {}

    e = conn.entries[0]
    name   = str(e.displayName) if e.displayName else str(e.cn)
    groups = [g.split(",")[0].replace("CN=", "") for g in (e.memberOf.values or [])]
    conn.unbind()

    return {"samaccount": str(e.sAMAccountName), "displayName": name,
            "email": str(e.mail), "department": str(e.department),
            "title": str(e.title), "groups": groups}
```

---

## 群組授權 Decorator

```python
from functools import wraps
from flask import jsonify

def require_group(*groups):
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            user = get_current_user()
            if not user:
                return jsonify({"error": "未登入"}), 401
            info = get_ad_user_info(user["samaccount"])
            if not any(g in info.get("groups", []) for g in groups):
                return jsonify({"error": "權限不足",
                                "required": list(groups)}), 403
            return f(*args, **kwargs)
        return wrapper
    return deco

# 使用方式
@app.route("/api/admin")
@require_group("IT-Admins")
def admin():
    return jsonify({"message": "管理員專區"})
```

---

## .env 設定對照

```ini
MOCK_AD=false                          # 正式環境
AD_SERVER=ldap://KH                    # NetBIOS 短名
AD_DOMAIN=kh.asegroup.com             # 完整網域
AD_BASE_DN=DC=kh,DC=asegroup,DC=com   # 搜尋根目錄
AD_BIND_DN=                            # 服務帳號 DN（選填）
AD_BIND_PW=                            # 服務帳號密碼（選填）
SECRET_KEY=<長亂數字串>                 # Flask session 金鑰
```

讀取方式（app.py 頂端）：
```python
import os
from dotenv import load_dotenv
load_dotenv()

AD_SERVER  = os.environ.get("AD_SERVER",  "ldap://KH")
AD_DOMAIN  = os.environ.get("AD_DOMAIN",  "")
AD_BASE_DN = os.environ.get("AD_BASE_DN", "")
AD_BIND_DN = os.environ.get("AD_BIND_DN", "")
AD_BIND_PW = os.environ.get("AD_BIND_PW", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me")
```
