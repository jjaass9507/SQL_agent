# 故障排除參考

## 診斷順序

遇到問題先收集這三樣：

```powershell
# 1. Python log（即時監看）
Get-Content "D:\WebServices\my-app\logs\python.log" -Tail 30 -Wait

# 2. IIS 事件記錄
Get-EventLog -LogName Application -Source "IIS*" -Newest 10

# 3. 用瀏覽器開診斷端點（確認 IIS 有傳入身分）
# http://<部署機IP>:<Port>/debug/env
```

> 診斷完成後記得移除 `/debug/env` 路由。

---

## 錯誤速查表

### 502 Bad Gateway

**現象**：瀏覽器出現「502 - Web server received an invalid response」

**原因**：Waitress 程序沒有啟動，HttpPlatformHandler 無法代理。

**排查步驟**：
1. 看 `logs\python.log` 有沒有內容
2. 手動測試能不能啟動：
   ```powershell
   .\venv\Scripts\python.exe -m waitress --port=9099 wsgi:application
   ```
3. 常見原因：
   - `processPath` 路徑錯誤（waitress-serve.exe 不在這裡）
   - `PYTHONPATH` 沒指向 wsgi.py 所在目錄
   - AppPool 帳號沒有讀取目錄的權限
   - `logs\` 目錄不存在或沒有寫入權限
   - venv 套件缺失（import 失敗）

---

### log 檔空白

**原因**：Python stdout 有緩衝，或 `logs\` 目錄權限不對。

**解法**：
- `web.config` 加上 `PYTHONUNBUFFERED=1`
- 確認 `logs\` 目錄存在且 AppPool 帳號有 Modify 權限

---

### Fatal error in launcher: Unable to create process using '...\python.exe'

**原因**：venv 是從另一個路徑複製過來的，venv 內的 `.exe` 寫死了建立時的絕對路徑。

**解法**：刪除 venv，在部署機的最終目錄重建：
```powershell
Remove-Item -Recurse -Force venv
python -m venv venv
.\venv\Scripts\pip install --no-index --find-links=wheels -r requirements.txt
```

---

### No Python at 'C:\Users\...\AppData\...\python.exe'

**原因**：Python 是「只給目前使用者」的安裝，IIS AppPool 帳號無法存取 AppData。

**解法**：重裝 Python，安裝時勾選「Install for all users」→ 安裝到 `C:\Program Files\PythonXX`。

---

### unsupported hash type MD4（或 unsupported hash type md4）

**原因**：Python 3.9+ 搭配 OpenSSL 3.0，MD4 演算法被停用。ldap3 的 NTLM bind 依賴 MD4。

**解法**：改用 SIMPLE bind，完全繞過 MD4：
```python
conn = Connection(server, user=f"KH\\{samaccount}", password=pw,
                  authentication=SIMPLE, auto_bind=True)
```
嘗試用 `openssl.cnf` 啟用 legacy provider 通常無效，因為 Python venv 的 OpenSSL 沒有附 legacy DLL。

---

### SSO 拿到 Administrator，不是真正的登入者

**原因**：使用了 `ImpersonateLoggedOnUser` + `GetUserNameEx` 方式。
AppPool 設為 LocalSystem 時，impersonation 不穩定，`GetUserNameEx` 回傳的是 IIS 程序自身的帳號。

**解法**：改用 `GetTokenInformation(TokenUser)` → `LookupAccountSid`，
直接從 token 讀 SID，不靠 impersonation。
見 [ad-auth.md](ad-auth.md#正確做法getTokenInformation--lookupAccountSid)。

---

### 登出後跳出 Windows 認證視窗

**原因**：`/auth/whoami` 或相關 API 在無身分時回了 `401`。
IIS 攔截到 401 會自動觸發瀏覽器的 Windows 認證提示。

**解法**：無身分狀態（`logged_out`）一律回 `200` + `{"auth_type": null}`，讓前端自行判斷顯示登入表單。

---

### 503 Service Unavailable

**原因**：AppPool 停止運作（可能因為程序崩潰超過重試次數）。

**解法**：
```powershell
# PowerShell
Start-WebAppPool -Name "Pool-my-app"
```
或 IIS 管理員 → 應用程式集區 → 右鍵「啟動」。

---

### 瀏覽器跳出 Windows 認證視窗（同網域電腦）

**原因**：瀏覽器沒有把這個站台列為「近端內部網路」，不會自動帶 Windows 憑證。

**解法**：
- 個人電腦：Internet 選項 → 安全性 → 近端內部網路 → 網站 → 加入站台 URL
- 全公司：用 GPO 統一派送「網站對區域指派清單」設定

---

### invalidCredentials（LDAP bind 失敗）

**原因**：帳號或密碼格式錯誤。

**排查**：
```powershell
# 先確認帳號在 AD 的實際格式
whoami
# → KH\K11879（NetBIOS\工號）表示正確格式
```

常見格式問題：
- 應用 `KH\工號` 但用了 `工號@kh.asegroup.com`（或反過來）
- sAMAccountName 和工號不同（有些 AD 不用工號當帳號名）

---

### SERVER_NAME: waitress.invalid

**原因**：你直接打到 Waitress 的動態 port（`%HTTP_PLATFORM_PORT%`），繞過了 IIS。

**解法**：用 IIS 站台的 Port（例如 `http://server:8001`）存取，
不要直接打 Waitress 的 port（9000-65535 隨機分配的那個）。

---

### cp950 UnicodeEncodeError

```
UnicodeEncodeError: 'cp950' codec can't encode character '\u2705'
```

**原因**：繁中 Windows 預設 cp950 編碼，不支援 Emoji 或部分 Unicode 字元。

**解法**：
1. `web.config` 加 `PYTHONIOENCODING=utf-8`
2. app.py 頂端加：
   ```python
   import sys
   if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
       sys.stdout.reconfigure(encoding="utf-8", errors="replace")
   ```
3. log 字串改用 `[OK]` / `[NG]` 取代 ✅ / ❌

---

## 常用診斷端點（開發 / 部署期間使用）

```python
@app.route("/debug/env")
def debug_env():
    """確認 IIS 傳入的身分資訊，上線前移除"""
    keys = ["REMOTE_USER", "AUTH_USER", "AUTH_TYPE",
            "HTTP_X_IIS_WINDOWSAUTHTOKEN", "SERVER_NAME"]
    result = {k: request.environ.get(k, "(無)") for k in keys}
    result["_auth_keys"] = {
        k: str(v) for k, v in request.environ.items()
        if any(x in k.upper() for x in ["USER", "AUTH", "LOGIN"])
    }
    return jsonify(result)

@app.route("/auth/whoami")
def whoami():
    """確認目前解析到的身分"""
    user = get_current_user()
    if not user:
        return jsonify({"auth_type": None,
                        "logged_out": session.get("logged_out", False)}), 200
    info = get_ad_user_info(user["samaccount"])
    return jsonify({"auth_type": user["auth_type"], **info})
```
