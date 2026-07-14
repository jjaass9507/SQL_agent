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
