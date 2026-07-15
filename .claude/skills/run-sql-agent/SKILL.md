---
name: run-sql-agent
description: Build, launch, drive, and deploy SQL Agent v2 (FastAPI/uvicorn web app). Use when asked to run / start / launch / smoke-test / screenshot the app locally, or to package it for OFFLINE deployment (pip download + venv + .pth live-source) onto a Windows Server IIS / AD SSO production machine without Docker. Covers alembic migrations, the [postgres] extra, and the pack/install/verify PowerShell scripts.
---

# Run & Deploy SQL Agent v2

FastAPI/uvicorn web app：對話式收集資料表需求、產文件、DB 審查、DB Agent。
本機用 SQLite；正式機用 PostgreSQL + Windows IIS（AD SSO），**不使用 Docker**。

- **本機驅動 handle**：`.claude/skills/run-sql-agent/smoke.sh`（背景起 uvicorn + curl 打端點）。
- **正式部署**：離線 wheel 打包 → venv → `.pth` 指向原始碼樹，見 [Deployment](#deployment)。

所有路徑相對於專案根目錄（含 `pyproject.toml` 那層）。

## Prerequisites

容器已有 Python 3.11。裝依賴（`.[postgres]` 才有 psycopg2/asyncpg；`dev` 只給測試/lint）：

```bash
pip install -e ".[dev]" ".[postgres]"
```

## Build

```bash
mkdir -p data                 # SQLite 檔的目錄，在 .gitignore，乾淨 checkout 沒有它
alembic upgrade head          # 建/升級資料表（本機預設 sqlite+aiosqlite:///./data/app.db）
```

驗證：`alembic current` 應顯示 `0003 (head)`。

## Run (agent path)

一鍵：起 app、打五個關鍵端點、全 200 才 exit 0，最後自動收掉行程。

```bash
bash .claude/skills/run-sql-agent/smoke.sh
```

實測輸出（本次執行）：

```
==> alembic upgrade head
==> 啟動 uvicorn on :8000
==> 驅動端點
  OK   /healthz [200]
  OK   / [200]
  OK   /docs [200]
  OK   /api/v1/settings [200]
  OK   /api/v1/llm/health [200]
PASS：app 可啟動並回應。LLM 端點 ok:false 屬正常（未設 gateway）。
```

改埠：`PORT=8010 bash .claude/skills/run-sql-agent/smoke.sh`。

### 手動起 + 單一端點

```bash
mkdir -p data && alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000 &   # 單一行程！不可 --workers N（見 Gotchas）
curl -s http://127.0.0.1:8000/api/v1/settings
# {"configured":false,"backend":"sqlite","masked_url":"sqlite+aiosqlite:///./data/app.db","business_databases":[]}
```

### 前端截圖（web）

`chromium` 在 `/opt/pw-browsers/`。起 server 後（`pip install playwright` 提供 Python API）：

```bash
python - <<'PY'
from playwright.sync_api import sync_playwright
import glob
chrome = glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome")[0]
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
    pg = b.new_page(viewport={"width":1280,"height":800})
    pg.goto("http://127.0.0.1:8000/", wait_until="networkidle", timeout=15000)
    pg.screenshot(path="frontend.png"); print("title:", pg.title()); b.close()
PY
```

首頁 title `SQL Agent — 首頁`，深色「Pro Space Gray」主題：側欄（首頁／資料庫助手／設定）、
工作台三顆按鈕、右下 DB Agent 泡泡。

## Deployment

正式部署 = 離線 wheel + venv + `.pth` live-source + IIS（**無 Docker**），三支 Windows
PowerShell 腳本在 `scripts/`。**機制本次已在 Linux 用等價 pip 指令端到端驗證**（`.ps1`
只是把同一串指令包成 Windows 版）；正式機請跑 `.ps1`。

**打包（連外機，Python 版本需與正式機相同）** — `scripts/pack_offline.ps1`。核心（本次實跑）：

```bash
# 關鍵：務必帶 [postgres] extra，否則正式機新增業務 DB 會噴 No module named 'psycopg2'
pip download ".[postgres]" -d offline_packages      # 抓到 psycopg2_binary + asyncpg + fastapi ...
pip wheel . --no-deps -w offline_packages           # 建 sql_agent-2.0.0a0-py3-none-any.whl
```
> 建 wheel **不要**加 `--no-build-isolation`：會撞到系統上被打補丁/過舊的 setuptools
> （實測 Debian 版炸 `install_layout`）。打包機連外，讓 pip 自備乾淨 build 後端。

**安裝（離線正式機，解壓到最終路徑後就地執行）** — `scripts/install_offline.ps1`。核心（本次實跑）：

```bash
python -m venv .venv                                            # 就地建，venv 不可搬移
.venv/bin/python -m pip install --no-index --find-links=offline_packages "sql-agent[postgres]"
.venv/bin/python -c "import psycopg2, asyncpg; print('deps OK')"
# .pth 機制：卸載專案本體、把 repo 根寫進 import 路徑 → 往後 git pull 即更新，免重裝
.venv/bin/python -m pip uninstall -y sql-agent
SP=$(.venv/bin/python -c "import sysconfig; print(sysconfig.get_path('purelib'))")
echo "$PWD" > "$SP/app.pth"
.venv/bin/python -c "import app, pathlib; print('app 載入自:', pathlib.Path(app.__file__).parent)"
# → app 載入自: <repo>/app
```

裝完在正式機：`copy .env.example .env` 填 `SECRET_KEY`/`DB_ENCRYPTION_KEY`/`DATABASE_URL`
/`LLM_*`/`AUTH_ENABLED=true`/`AD_*` → `python -m alembic upgrade head` → 由 IIS 經
`web.config` 拉起 uvicorn（`docs/deployment.md` 4-2，`processPath` 指本 `.venv` 的 python）。

**驗證正式機跑的是哪份程式** — `scripts/verify_deploy.ps1`：顯示 checkout、`git pull`、
確認 `import app` 來自原始碼樹而非 site-packages 殘留，通過後回收 IIS 應用程式集區。

完整 IIS/AD SSO 建置見 `docs/deployment.md` 第 4 節；本機排錯見 `docs/troubleshooting.md`。

## Test

```bash
python -m pytest -q          # 460 passed, 1 skipped
ruff check .
```

## Gotchas

- **`data/` 不存在 → sqlite `unable to open database file`。** `data/` 在 `.gitignore`，
  乾淨 checkout 沒有它。先 `mkdir -p data` 再 `alembic upgrade head`。
- **新增業務 DB 報 `No module named 'psycopg2'`。** psycopg2-binary 在 `postgres` extra，
  `.[dev]` 不含。裝 `pip install -e ".[postgres]"`；離線打包務必 `pip download ".[postgres]"`。
- **只能單一 uvicorn 行程，不可 `--workers N`。** 背景 job worker 跑在 FastAPI 的 event
  loop 內，無跨 process 鎖；多 worker 會重複處理 job 表。IIS 站台也只綁一個後端行程。
- **`pip wheel .` 加 `--no-build-isolation` 會炸 `AttributeError: install_layout`**（系統
  setuptools 太舊/被打補丁）。拿掉該旗標用 build isolation。
- **venv 不可跨機器/路徑搬移**（`.exe`/`.pth` 內嵌絕對路徑）。一律在最終部署路徑就地建。
- **`.pth` live-source**：安裝後 `import app` 必須解析到原始碼樹的 `app/`；若指到
  site-packages 殘留副本，`git pull` 不會生效——`verify_deploy.ps1` 專抓這個。
- **`LLM_*` 改了沒重啟 = 沒生效**（`app/config.py` 有 `@lru_cache`）。改 `.env` 後重啟 uvicorn。
- **`/api/v1/llm/health` 回 `ok:false`** 在未設 gateway 時屬正常，不是壞掉。

## Troubleshooting

| 症狀 | 修法 |
|---|---|
| `sqlite3.OperationalError: unable to open database file` | `mkdir -p data` 後再 alembic |
| `Could not parse SQLAlchemy URL` | `.env` 有空的 `DATABASE_URL=`，刪掉或補回預設 |
| 新增業務 DB `No module named 'psycopg2'` | `pip install -e ".[postgres]"` |
| `pip wheel` 報 `install_layout` | 拿掉 `--no-build-isolation` |
| 正式機「bug 修好又出現」 | 跑 `scripts/verify_deploy.ps1`，多半是 site-packages 殘留舊副本 |
| LLM gateway `Connection error`（TLS/proxy/路徑） | 見 `docs/troubleshooting.md` 第 3–5 節 |
