---
name: python-iis-ad-deploy
description: >
  Deploy Python (Flask/FastAPI/Django) on Windows Server IIS with HttpPlatformHandler +
  Waitress and Active Directory Windows Authentication. Use whenever the user mentions:
  Python on IIS, HttpPlatformHandler, web.config for Waitress, Windows Auth / AD SSO,
  ldap3 bind, REMOTE_USER, X-IIS-WindowsAuthToken, forwardWindowsAuthToken, offline
  wheel deployment, or errors like "Fatal error in launcher", "unsupported hash type MD4",
  "No Python at AppData", or "502 Bad Gateway" on IIS+Python. Covers the full lifecycle:
  architecture, pre-deploy checklist, IIS setup (PowerShell + UI), venv + offline wheels,
  web.config, AD token decoding, SIMPLE bind login, logout/switch-user, and troubleshooting.
---

# Python + IIS + AD 部署 Skill

這份 skill 記錄了在 **Windows Server 2019/2022** 上，以 Microsoft 官方推薦的
**HttpPlatformHandler + Waitress** 架構部署 Python Web App，並整合 **Active Directory
Windows 驗證**的完整做法。每個關鍵決策都附有「為什麼這樣做」的說明，避免重踩實際
部署中遭遇的坑。

## 使用這份 skill 的時機

| 情境 | 直接跳到 |
|------|---------|
| 想了解架構選型原因 | [架構決策](#架構決策) |
| 已有專案，要在部署機上操作 | [部署 Checklist](#部署-checklist) → 逐步參考 |
| 只需要 web.config 範本 | [references/web-config.md](references/web-config.md) |
| AD 認證整合（SSO / 手動登入） | [references/ad-auth.md](references/ad-auth.md) |
| 遇到錯誤訊息 | [references/troubleshooting.md](references/troubleshooting.md) |
| 需要 PowerShell 指令或 IIS UI 步驟 | [references/iis-setup.md](references/iis-setup.md) |
| 內網隔離，套件無法連網安裝 | [references/offline-deploy.md](references/offline-deploy.md) |

---

## 架構決策

### 為什麼選 HttpPlatformHandler + Waitress

**HttpPlatformHandler**（IIS 官方模組，需單獨下載 MSI）：
- Microsoft 官方推薦，取代已停止維護的 wfastcgi / FastCGI
- IIS 直接管理 Python 程序生命週期（自動啟動、崩潰重啟、停止時連帶終止）
- 不需要額外的 NSSM 或 WinSW 來當服務管理器
- 透過動態 port（`%HTTP_PLATFORM_PORT%`）代理請求
- 設定 `forwardWindowsAuthToken="true"` 後，IIS 驗證結果以 token handle 傳給 Python

**Waitress**（WSGI Server）：
- 純 Python、Windows 友善、無 C extension 依賴
- 生產品質，支援多執行緒（`--threads=4`）
- 不需要額外設定，直接與 HttpPlatformHandler 搭配

### 不用 WinSW / NSSM

這兩個工具適合非 HTTP 的背景程序（排程 worker、queue consumer 等）。
HTTP Web App 讓 HttpPlatformHandler 管理就夠了。

### 一機多服務

每個服務各自獨立：一個部署目錄 + 一個 venv + 一個 AppPool。
詳見 [references/iis-setup.md](references/iis-setup.md#多服務隔離)。

---

## 部署 Checklist

### 前提：專案程式碼已 Ready 的結構

```
your-project/
├── app.py              ← 你的 Flask/FastAPI/Django app
├── wsgi.py             ← Waitress 進入點（見下方範本）
├── requirements.txt
├── .env                ← AD 連線參數（不進 git）
├── .env.example        ← 範本（進 git）
├── .gitignore          ← 含 venv/, .env, logs/, wheels/
├── wheels/             ← pip download 預先打包（離線環境用）
├── templates/  static/ ← 你的 UI 資源
└── web.config          ← 本次部署新增（見 references/web-config.md）
```

**wsgi.py 固定格式**（只需修改 import 來源）：
```python
# wsgi.py
from your_app_module import app as application
# Waitress 執行：wsgi:application
```

### 部署前需確認的資訊

| 資訊 | 說明 |
|------|------|
| 部署目錄路徑 | 例：`D:\WebServices\my-app` |
| IIS 站台 Port | 例：`8001`（或 80） |
| AD 伺服器名稱 | NetBIOS 短名，例：`KH` |
| AD 網域 | 例：`kh.asegroup.com` |
| AD BaseDN | 例：`DC=kh,DC=asegroup,DC=com`（由網域推導） |
| Python 版本 | 必須與 wheels/ 打包時相同 |
| 服務帳號（選填） | 查詢群組 / Email 用 |

**AD_BASE_DN 推導規則**：把網域以 `.` 拆開，每段加 `DC=`，逗號合併
→ `kh.asegroup.com` 變成 `DC=kh,DC=asegroup,DC=com`

### 部署步驟概覽

```
Step 1  安裝 IIS 功能 + HttpPlatformHandler + Python
Step 2  建立 venv + 安裝套件（離線：from wheels/）+ 建立 logs\
Step 3  新增 web.config（唯一需要新增的檔案）
Step 4  建立 IIS AppPool + 網站（PowerShell 或 UI）
Step 5  設定 Windows 驗證 + 目錄權限 → iisreset
Step 6  驗證：log → /debug/env → /auth/whoami → 測試登出切換
```

每個步驟的詳細指令（PowerShell + IIS UI 雙版本）見
→ [references/iis-setup.md](references/iis-setup.md)

---

## AD 認證整合要點

詳細說明見 [references/ad-auth.md](references/ad-auth.md)，以下是關鍵決策摘要：

### SSO 身分解析（自動登入，無需密碼）

1. IIS 驗證瀏覽器的 Windows 憑證（Kerberos/NTLM）
2. `forwardWindowsAuthToken="true"` → token handle 以 `HTTP_X_IIS_WINDOWSAUTHTOKEN` 傳給 Python
3. Python 用 `GetTokenInformation(TokenUser)` 從 token 讀出 SID
4. `LookupAccountSid` 將 SID 反查成帳號名稱

> ⚠️ **不要用** `ImpersonateLoggedOnUser` + `GetUserNameEx`：
> 在 AppPool 為 LocalSystem 時會拿到 IIS 程序身分（`Administrator`），不是登入者。

### 手動 AD 登入（切換帳號用）

```python
from ldap3 import Server, Connection, NONE, SIMPLE

netbios  = AD_SERVER.replace("ldap://", "").upper()   # ldap://KH → KH
user_str = f"{netbios}\\{samaccount}"                 # e.g. KH\K11879
server   = Server(AD_SERVER, get_info=NONE)           # NONE：不拉 schema，更快
conn     = Connection(server, user=user_str, password=pw,
                      authentication=SIMPLE, auto_bind=True)
conn.unbind()  # bind 成功 = 密碼正確
```

> ⚠️ **用 SIMPLE，不用 NTLM**：Python 3.9+ / OpenSSL 3.0 停用 MD4，
> NTLM bind 會直接炸掉（`unsupported hash type MD4`）。

### requirements.txt 必要套件

```
flask          # 或 fastapi / django
waitress       # WSGI server
ldap3          # AD/LDAP 查詢與驗證
python-dotenv  # 讀取 .env
pywin32        # Windows token 解析
```

---

## 常見錯誤快速對照

| 錯誤訊息 | 解法 |
|---------|------|
| `502 Bad Gateway` | 看 log；確認 processPath / PYTHONPATH / 目錄權限 |
| log 檔空白 | `web.config` 加 `PYTHONUNBUFFERED=1` |
| `Fatal error in launcher` | 刪 venv，在部署機最終路徑重建 |
| `No Python at '...AppData'` | Python 重裝，勾「Install for all users」 |
| `unsupported hash type MD4` | ldap3 改用 SIMPLE bind，不用 NTLM |
| SSO 拿到 `Administrator` | 改用 `GetTokenInformation` 取 SID |
| 登出後跳 Windows 認證視窗 | `logged_out` 狀態回 `200`，不回 `401` |
| `503 Service Unavailable` | AppPool 停止；IIS 管理員重新啟動 AppPool |

完整故障排除見 → [references/troubleshooting.md](references/troubleshooting.md)
