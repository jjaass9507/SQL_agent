# SQL Agent v2 — 部署指南

## 1. 本機開發（零設定）

不需要 Docker、不需要 PostgreSQL。預設 `DATABASE_URL` 指向本機 SQLite。

```bash
pip install -e ".[dev]"
cp .env.example .env   # 可選；不改也能跑，會用 config.py 的預設值
alembic upgrade head    # 建立 SQLite 資料表
uvicorn app.main:app --reload
```

啟動後 `http://127.0.0.1:8000/healthz` 應回 `{"status": "ok"}`。

若要接真實 LLM gateway，於 `.env` 填入 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`。
未設定 gateway 時，LLM 相關端點會回錯誤，但其餘功能（含健康檢查、靜態頁面）不受影響。

## 2. Docker Compose（app + PostgreSQL）

```bash
cp .env.example .env   # 至少填入 SECRET_KEY / DB_ENCRYPTION_KEY / LLM_* / ADMIN_TOKEN
docker compose up --build
```

`docker-compose.yml` 會啟動：

- `postgres`：PostgreSQL 16，帶 healthcheck（`pg_isready`），資料存於具名 volume `postgres_data`
- `app`：`Dockerfile` build 出的 image，`depends_on: postgres` 且等 `service_healthy` 才啟動；
  `DATABASE_URL` 已在 compose 中固定指向該 postgres 服務（`postgresql+asyncpg://...`），
  不需在 `.env` 另外設定

首次啟動或 schema 變更後，需手動跑 migration（容器內或本機均可，只要 `DATABASE_URL` 指向同一個 DB）：

```bash
docker compose exec app alembic upgrade head
```

`app` 服務對外映射 `8000:8000`，啟動後可用 `http://localhost:8000/healthz` 確認。

## 3. 正式環境注意事項

### TLS

容器內的 uvicorn **不**處理 TLS。正式環境一律在前面加反向代理（nginx / Caddy / Traefik /
雲端 LB）終止 TLS，代理再以明文轉發到容器的 8000 port。不要把憑證塞進 image 或直接對外裸露
uvicorn。

### 資料庫 migration

正式環境一律用 Alembic 管理 schema，不使用 `app.repos.db.init_db()`（那只是給測試/本機快速起手用）：

```bash
alembic upgrade head
```

部署流程建議在啟動新版本容器**之前**先對正式 DB 跑一次 `alembic upgrade head`（或作為
deploy pipeline 的獨立步驟），確認遷移成功後才切換流量。

### 單 worker 限制（重要）

`Dockerfile` 的 CMD 是：

```
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**刻意不加 `--workers N`。** 原因：v2 的背景工作機制（見
`docs/v2_rebuild_plan.md` §3-1、§3-3 `app/workers/`）設計為「DB-backed `jobs` 表 + 程序內
asyncio worker」——job runner 跑在 FastAPI process 的 event loop 裡，不是獨立的程序。若用
`--workers N`（N > 1）啟動 uvicorn，每個 worker process 都會各自認為自己該處理 job 表，
目前尚未實作跨 process 的鎖/協調機制，會導致 job 被重複處理或行為不可預期。

因此：

- **目前建議單 worker 部署**（本 compose 設定即單 worker）。
- 若需要橫向擴充 HTTP 吞吐量，之後應該把「job runner」拆成獨立程序 / 獨立部署單元（計畫書中
  `JobRunner` 介面即為此預留），HTTP 層則可安全地開多 worker 或多副本；只要保證 job runner
  只在其中一個程序啟動即可。
- 在 job runner 拆分完成之前，**不要**用多 worker/多副本部署本 image，否則背景任務
  （生成、審查等）的正確性沒有保證。

### 環境變數

所有環境變數的權威定義在 `app/config.py`；範例與說明見專案根目錄的 `.env.example`。
務必在正式環境更換 `SECRET_KEY`，並產生專屬的 `DB_ENCRYPTION_KEY`：

```bash
python -c "import os; print(os.urandom(32).hex())"
```

`ADMIN_TOKEN` 是 Phase 7 正式使用者/角色系統上線前的過渡機制；上線後應視 Phase 7 進度決定
是否保留。

## 4. Windows Server IIS 部署（AD SSO）

適用情境：內網部署、需要以 Active Directory 帳號登入，且希望瀏覽器透過 IIS 的
Windows Authentication 自動帶入身分（SSO，免再輸入密碼）。此路徑與第 1／2 節的
uvicorn 直跑／Docker Compose 部署互斥——選這條路徑時，對外服務窗口是 IIS，
`uvicorn` 只在 IIS 背後以 `HttpPlatformHandler` 方式被拉起，不直接對外監聽。

> **權威參考**：本章是針對本專案（FastAPI/uvicorn）的摘要；完整的 IIS + AD
> 建置細節（IIS 角色安裝、AD 認證程式碼範式、離線 wheel、疑難排解全集）以
> 使用者提供的實戰 skill 為準，全文收錄於 [`docs/iis_ad_deploy/`](iis_ad_deploy/SKILL.md)。

### 4-1 前置需求

- Windows Server 已加入要驗證使用者所在的 AD 網域。
- IIS 已安裝角色：Web Server (IIS)、**Windows Authentication**（在「新增角色及功能」
  的 Web Server → Security 底下勾選；預設不裝）。
- 安裝 [HttpPlatformHandler](https://www.iis.net/downloads/microsoft/httpplatformhandler)
  （IIS 模組，讓 IIS 把請求轉發給任意可執行檔啟動的後端行程，這裡是 venv 裡的
  `python.exe` + `uvicorn`）。
- 目標機器上準備好一份與正式環境 Python 版本相符的 venv，且已用離線 wheel 裝好
  本專案依賴（見 4-4；正式內網機器通常不能直接 `pip install` 連外）。

### 4-2 web.config（HttpPlatformHandler 設定 uvicorn 行程）

站台實體目錄放本專案原始碼（或部署產物），根目錄 `web.config` 內容：

```xml
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <system.webServer>
    <handlers>
      <add name="httpPlatformHandler" path="*" verb="*"
           modules="httpPlatformHandler" resourceType="Unspecified" />
    </handlers>
    <httpPlatform processPath="C:\inetpub\sql_agent\venv\Scripts\python.exe"
                   arguments="-m uvicorn app.main:app --host 127.0.0.1 --port %HTTP_PLATFORM_PORT%"
                   workingDirectory="C:\inetpub\sql_agent"
                   startupTimeLimit="60"
                   startupRetryCount="3"
                   stdoutLogEnabled="true"
                   stdoutLogFile="C:\inetpub\sql_agent\logs\stdout"
                   forwardWindowsAuthToken="true">
      <environmentVariables>
        <environmentVariable name="AUTH_ENABLED" value="true" />
        <!-- 關閉 stdout 緩衝：沒設的話 log 檔會一片空白（輸出被 buffer 住） -->
        <environmentVariable name="PYTHONUNBUFFERED" value="1" />
        <!-- 繁中 Windows 預設 cp950，遇到 emoji/特殊字元會讓行程崩潰，強制 UTF-8 -->
        <environmentVariable name="PYTHONIOENCODING" value="utf-8" />
      </environmentVariables>
    </httpPlatform>
  </system.webServer>
</configuration>
```

重點：

- `processPath` 一律指向該站台專屬 **venv** 內的 `python.exe`（不要用系統全域
  Python）——版本、已安裝套件才會對得上部署時測試過的環境。
- `arguments` 直接跑 `uvicorn app.main:app`，port 用 IIS 動態分配的
  `%HTTP_PLATFORM_PORT%`（HttpPlatformHandler 保留字，不要寫死埠號）；host 綁
  `127.0.0.1`——這個 uvicorn 行程只服務同機的 IIS 反向代理，不直接對外。
- `forwardWindowsAuthToken="true"`——這是 SSO 能運作的關鍵：IIS 完成 Windows
  Authentication 後，把驗證到的使用者 access token 轉交給後端行程，後端才能在
  `GET /api/v1/auth/sso` 讀到目前登入的 Windows 身分並換發應用程式自己的 session。
  沒開這個，SSO 端點永遠拿不到身分。
- `stdoutLogEnabled` + `stdoutLogFile`：開發期或初次部署務必開著，HttpPlatformHandler
  啟動失敗（例如 python.exe 路徑錯、套件缺漏）時，唯一的診斷線索通常就在這個
  log 檔，IIS 本身只會回 502 不會告訴你原因。

### 4-3 IIS 站台：啟用 Windows Authentication

1. IIS 管理員 → 選到該站台 → Authentication。
2. **Windows Authentication**：Enabled。
3. **Anonymous Authentication**：視需求二選一——
   - 全站都要求 AD 身分：停用 Anonymous Authentication。
   - 混合模式（例如健康檢查 `/healthz` 想維持匿名可探測、其餘走 AD）：兩者都開啟，
     並在 `web.config` 對特定路徑（如 `/healthz`）加 `<location>` 區塊覆寫成只允許
     Anonymous，其餘沿用站台預設的 Windows Authentication。

**信任邊界警告**：`forwardWindowsAuthToken` 與 `GET /api/v1/auth/sso` 這條路徑，
安全性完全建立在「這個 FastAPI 行程只能經由本機 IIS 存取」的前提上——`127.0.0.1`
binding 就是為了確保這件事。**絕對不要**把這個 uvicorn 行程直接對外网（例如另外開
`0.0.0.0` 的埠、或在防火牆開洞讓外部直連），也不要在其他反向代理後面裸露這個
SSO 端點，否則任何能直接打到這個行程的人都能偽造/繞過 IIS 的身分驗證結果。
只有 IIS 前面終止的請求，其攜帶的驗證資訊才可信。

### 4-4 離線安裝（無法連外的正式內網機器）

在一台**能連外、且 Python 版本與目標機器相同**的機器上，先把依賴下載成 wheel：

```powershell
pip download -r requirements.txt -d .\offline_wheels
# 或直接用 pyproject.toml 定義的套件：
pip download . -d .\offline_wheels
```

把 `offline_wheels` 資料夾整個複製到目標機器，離線安裝：

```powershell
pip install --no-index --find-links=.\offline_wheels -e .
```

常見錯誤與排查：

- **`Fatal error in launcher: Unable to create process`**（執行 `pip` / `uvicorn`
  等進入點指令時炸掉）：通常是 venv 建立後整個資料夾被**搬動或重新命名過**
  ——venv 裡的 `.exe` launcher 內嵌了建立當下的絕對路徑。修法：在目標機器最終
  路徑上**原地**建立 venv（`python -m venv C:\inetpub\sql_agent\venv`），不要在
  別處建好再搬過去。
- **`error: (hash algorithm) MD4` 或安裝 `cryptography` / 相依套件時的雜湊演算法
  錯誤**：Windows 內建 FIPS 模式或某些精簡版 OpenSSL build 未啟用 MD4，而部分
  舊版 wheel 的元資料驗證路徑會用到它。修法：確認下載 wheel 時用的是官方
  `cp3xx-win_amd64` 平台 wheel（不要混用 source distribution 現場編譯)，且
  `pip` / `setuptools` / `wheel` 版本夠新（連外機器上先 `pip install -U pip
  setuptools wheel` 再 `pip download`）；必要時同時下載對應版本的
  `cryptography` 官方 wheel 明確指定版本避免被解析成 sdist。
- **502 Bad Gateway**（IIS 回應，不是應用程式回的）：代表 HttpPlatformHandler
  沒能成功啟動或維持住後端行程。依序排查：(1) 看 4-2 設的
  `stdoutLogFile`，通常已經寫出 Python 端的 traceback；(2) 確認 `processPath`
  指到的 `python.exe` 在該帳號權限下真的能執行（IIS 應用程式集區的執行身分要
  對 venv 目錄與專案目錄有讀取/執行權限）；(3) 確認 `startupTimeLimit` 夠長
  ——第一次啟動要跑 alembic/import 較久時，預設值可能不夠，會被判定啟動失敗
  而被 IIS 秒殺行程。

### 4-5 環境變數

除了第 3 節「環境變數」通用內容外，AD SSO 部署另外要設：

```
AUTH_ENABLED=true
```

開啟後 `/api/v1/auth/*` 以外的端點才會要求／驗證 JWT（見 `app/api/routers/auth.py`
檔頭註解）。AD 綁定所需的 LDAP 連線參數（伺服器位址、Bind DN、Search Base 等，
命名慣例為 `AD_*`）由負責 ldap3 AD 驗證後端的分支新增進 `.env.example`——該分支
合併後，以 `.env.example` 當時的內容為準，本文件不重複列出實際變數名稱以免與
後端實作脫節。前端不需要，也不應該讀取任何 `AD_*` 變數——AD 連線與密碼驗證全部
在後端行程內完成，瀏覽器只透過本文件描述的 `/api/v1/auth/*` 端點與 `/login`
頁面互動。
