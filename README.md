# SQL Agent — 資料庫建檔管理 Agent 系統

透過對話式 AI，收集資料表設計需求後自動產出四份技術文件：規格書、ER Diagram、DDL 腳本、效能安全規劃。

提供兩種操作介面：
- **網頁平台**（`app.py`）— Flask 網頁介面，含對話、確認、文件檢視頁面
- **CLI 工具**（`main.py`）— 終端機命令列介面

---

## 功能概覽

### 設計模式（從零開始設計資料表）

```
使用者輸入需求
     ↓
Interviewer Agent 追問細節（欄位型態、主鍵、關聯、索引…）
     ↓
AI 整合雙方對話產出需求摘要 + Schema 確認頁（含與現有 DB 的 Diff）
     ↓
自動並行產出四個檔案
```

| 輸出檔案 | 內容 |
|---|---|
| `01_specification.md` | 資料庫規格書與資料字典（欄位表格） |
| `02_er_diagram.md` | Mermaid ER Diagram（網頁直接渲染） |
| `03_ddl.sql` | PostgreSQL DDL + 索引 + Migration + Seed Data |
| `04_security_plan.md` | 索引策略、存取控制、敏感欄位加密建議 |

### 審查模式（分析現有資料庫）

```
輸入現有 PostgreSQL 連線字串
     ↓
AI 分析現有 DB 結構（設計一致性 / 資料完整性 / 效能 / 安全）
     ↓
輸出 05_review_report.md 審查報告（含整體評分）
     + 規則式紅旗（警告/建議）與 06_review_fix.sql 修復腳本
```

### 資料庫助手（DB Agent，`/db-agent`）

全域對話式助手，針對設定頁設定的業務資料庫進行多步驟推理（ReAct 迴圈），取代早期「一問一答」的單發式設計：

```
使用者提問
     ↓
LLM 視需要連續呼叫工具（最多 8 步）：
  list_databases / get_schema / get_table_ddl / run_query / explain_query / analyze_schema /
  check_conventions / find_related_tables / check_table_docs / draft_comment_ddl / propose_ddl
     ↓
每次工具結果都截斷後回饋給 LLM 繼續推理
     ↓
propose_ddl 是 terminal 工具：呼叫後立即結束本回合，回覆附提案編號（見下方「人工審批」）
     │
     └─ 其他情況：LLM 輸出 <FINAL> 結束本回合，回覆可能包含：
          - 一般文字說明
          - <DESIGN_REQUEST>（偵測到「新建資料表」意圖 → 自動開一個設計 session）
```

- **對話持久化**：整個對話（含每一步工具呼叫與結果）存在 `session_store` 的單一全域 session（`mode="agent"`），伺服器重啟或多個 worker 之間不會遺失，也不再需要進程內單例
- **前端步驟軌跡**：每則回覆下方有可折疊的「已呼叫 N 個工具」列表，列出工具名稱與結果摘要
- 查詢仍遵循既有安全層（`web/sql_safety.py`）：`run_query` 僅接受單一 SELECT/EXPLAIN
- **結構變更一律經人工審批**：agent 不會直接執行任何 DDL，一律透過 `propose_ddl` 工具（或頁面上「送審」按鈕）建立待審變更請求，詳見下方「人工審批（變更請求）」一節

### 其他功能

- **設計版本管理**：每次修改後自動快照，可從確認頁一鍵還原任意版本
- **Schema Diff**：確認頁自動比對設計 Schema 與現有 DB 結構，列出新增 / 刪除 / 變更欄位
- **建表標準一致性檢查**：以多數決從現有 DB 推斷命名風格、PK、時間欄位等慣例，比對新設計是否一致（`web/convention_checker.py`），確認頁警告與 Interviewer 均會提示
- **需求與現有資料表關聯分析**：確定性計分找出可重用的表、建議的外鍵、重複建表風險（`web/table_relation.py`），顯示於確認頁「與現有資料庫的關聯」區塊
- **既有資料表資訊完整性檢查**：統計現有表的用途說明（table comment）與欄位說明（column comment）覆蓋率，DB Agent 可草擬缺漏的 `COMMENT ON` 語句供確認（`web/metadata_checker.py`；DB Agent 頁「📋 檢查文件完整性」快捷鈕）
- **現有 DB 匯入**：可匯入 PostgreSQL 結構作為設計參考（支援 10 / 30 / 30+ 張表三段精簡模式）
- **On-demand 延伸產出**：文件頁可按需產生 ORM 模型、Alembic migration、查詢範例；若有匯入現有 DB，另可產生 **增量 Migration（現有 DB → 設計的 ALTER 腳本）**
- **匯出格式**：一鍵匯出 DBML（dbdiagram.io）、PlantUML ER 圖、JSON Schema、資料字典 CSV（純模板、零 API 成本）
- **DDL Dry-run 驗證**：在文件頁 DDL 分頁按「驗證 DDL」，把 `03_ddl.sql` 套用到暫存 schema 並回滾，回報語法/相依錯誤（需 session 的資料庫或設定頁的平台 PostgreSQL）

### 人工審批（變更請求）

DB Agent 提出的所有結構變更（`propose_ddl` 工具，或頁面上把一則 DDL 建議按「送審」）都不會直接執行，而是
建立一筆待審的 change request：

1. `sql_safety.check_ddl_allowlist` + `ddl_validator.validate_ddl`（dry-run）驗證通過才會建立請求，否則直接回錯誤、不建立
2. 請求以 `pending` 狀態存於 `web/change_requests.py`（PostgreSQL 模式存 `change_requests` 表；JSON 模式存 `data/change_requests.json`）
3. 管理員在 DB Agent 頁「待審變更請求」面板核准/駁回，需在頁面輸入 `ADMIN_TOKEN`（存於瀏覽器 `sessionStorage`，隨請求以 `X-Admin-Token` header 送出）
4. 核准時重新驗證一次（避免資料庫已 drift）並在單一交易內執行（`web/ddl_executor.py`），結果記入 `activity_log`；駁回的請求永不執行

見下方「設定」的 `ADMIN_TOKEN` 環境變數，以及 `docs/architecture.md`「人工審批（HITL）變更請求」一節的完整流程圖。

---

## 環境需求

- Python 3.11+
- 一個 OpenAI 相容 Chat Completions API 端點（`base_url` + `api_key` + `model`）

---

## 安裝

```bash
git clone <repo-url>
cd SQL_agent

pip install -r requirements.txt

cp .env.example .env
# 編輯 .env，填入你的 LLM API 資訊
```

---

## 設定（`.env`）

```env
LLM_BASE_URL=https://your-llm-gateway.example.com/v1
LLM_API_KEY=your_api_key_here
LLM_MODEL=your_model_here
LLM_VERIFY=false
```

| 變數 | 必填 | 說明 |
|---|---|---|
| `LLM_BASE_URL` | ✓ | OpenAI 相容 Chat Completions API 端點。貼「v1 base」或整段「完整 completions 端點」（`.../chat/completions`）皆可，程式會自動正規化 |
| `LLM_API_KEY` | ✓ | API 金鑰（僅放 `.env`，不得寫入任何程式碼或文件） |
| `LLM_MODEL` | ✓ | 模型名稱 |
| `LLM_VERIFY` | | SSL 憑證驗證（預設 `false`，供自簽憑證端點使用） |

> **連線疑難排解**：啟動後可用 `GET /api/llm/health` 實際打一次 gateway，
> 回傳成功或完整失敗原因（連線錯誤類型、HTTP 狀態碼、回應片段）。
> 對 LLM 的請求一律**繞過系統 HTTP(S) proxy**（內網 gateway 場景），
> connect timeout 10 秒——主機不通會快速失敗並寫入 log，而非長時間無回應。
>
> `LLM_BASE_URL` 不論貼「v1 base」（如 `.../v1`）或某些內網 gateway
> 提供的「完整 completions 端點」（如 `.../v1/chat/completions`），都會被
> 自動正規化，不會組出重複的 `/chat/completions/chat/completions`。
>
> 若曾在同一個終端機（例如 PowerShell）手動 `export`／`$env:` 設過
> `LLM_VERIFY`、`LLM_BASE_URL` 等變數，編輯 `.env` 後現在會**覆蓋**這些
> 殘留的環境變數（`load_dotenv(override=True)`），不需要另開新終端機。
| `DATABASE_URL` | | 設定後 Session 改存 PostgreSQL；未設定則用 `data/*.json` |
| `DATA_DIR` | | JSON 模式的資料目錄（預設 `data/`） |
| `ADMIN_TOKEN` | | 核准/駁回 DB Agent 變更請求所需的共享密鑰（`X-Admin-Token` header）。未設定時，`/api/change-requests/<id>/approve` 與 `.../reject` 一律回 403 |
| `SECRET_KEY` | | Flask session 金鑰。**非 debug 模式**（`FLASK_DEBUG` 未開）下若仍是 `.env.example` 內的預設值，`python app.py` 啟動時會直接報錯退出 |
| `FLASK_DEBUG` | | 設為 `1`/`true` 開啟 Flask debug 模式（含自動重載、debugger）。預設關閉 |
| `HOST` | | `python app.py` 綁定的位址，預設 `127.0.0.1`（僅限本機）。對外服務時可設為 `0.0.0.0`，建議搭配反向代理 |

---

## 儲存後端

平台支援兩種 Session 儲存模式：

- **PostgreSQL 模式**：使用 SQLAlchemy Core，Session 存於 `sessions` / `messages`
  資料表，支援連線池與多 worker 部署。連線來源有兩種（設定頁優先於環境變數）：

  - **設定頁（建議）**：開啟側欄「⚙ 設定」，填入 PostgreSQL 連線字串並儲存。
    系統會測試連線、自動建立所需資料表，之後平台的所有專案與對話即存入該資料庫作為記憶。
    連線字串存於本機 `data/app_settings.json`（git ignored），回傳前端時密碼一律遮罩。

  - **環境變數**：部署時可設 `DATABASE_URL`：

    ```bash
    export DATABASE_URL=postgresql://user:pass@host:5432/sql_agent
    alembic upgrade head   # 用 Alembic 管理 schema 的團隊；非必要（見下）
    ```

  > **Schema 自我修復**：取得連線時 `web/db_schema.py:ensure_schema()` 會自動建立缺少的
  > 資料表，並以 `ALTER TABLE ... ADD COLUMN` 補上既有表缺少的新增欄位（idempotent）。
  > 因此升級版本後**不需手動跑 Alembic**也能自動補欄位；Alembic migration 仍保留供需要者使用。

  連接資料庫後，平台會把使用紀錄（建立／確認設計、完成審查、執行查詢、設定變更等）
  寫入 `activity_log` 資料表，作為操作軌跡，可由 `GET /api/activity` 查詢。

- **JSON 檔案模式**（未設定上述任一者，預設）：Session 存於 `data/*.json`，
  零外部相依，適合本地開發與測試。

兩種模式的 `web/session_store.py` 對外介面完全相同，切換不需改動其他程式碼。

---

## 使用方式

### 網頁平台（建議）

```bash
python app.py
# 開啟瀏覽器 http://localhost:5000
```

預設綁定 `127.0.0.1`、關閉 debug 模式。本機開發需要自動重載時設 `FLASK_DEBUG=1`；
**非 debug 模式**若 `SECRET_KEY` 仍是 `.env.example` 的預設值，啟動會直接報錯退出（見上方環境變數表）。
對外服務／生產環境請用 Gunicorn（`gunicorn app:app`），不要直接跑 `python app.py`。

**設計模式**（預設）：首頁（專案管理）→ 對話頁（需求收集）→ 確認頁（Schema 審閱 + Diff + 版本管理）→ 文件頁（即時進度 + 預覽/下載）。

**審查模式**：首頁（選「🔍 審查模式」並填入 DB 連線字串）→ 審查頁（AI 自動分析並輸出報告）。

**DDL 匯入**：首頁（選「📋 DDL 匯入」）貼上既有的 `CREATE TABLE` 語句，系統解析成 Schema 後
直接進入確認頁，可調整後產出文件——適合已有資料庫、想快速補文件的情境。

確認頁會以規則式「設計顧問」即時標出常見問題（缺主鍵、外鍵未建索引、疑似唯一值未加 UNIQUE、
明文密碼、camelCase 命名、泛用 JSON 欄位、缺軟刪除欄位等），不需 LLM。

**SQL 工作台**：建立 Session 時填入資料庫連線字串後，文件頁會出現「⚙ SQL 工作台」分頁：
左側為結構瀏覽器（資料表／欄位樹，標示 PK/FK，點欄位插入名稱、雙擊資料表帶入 SELECT 範本），
右側可執行唯讀查詢與 `EXPLAIN`、查看查詢記錄、匯出 CSV，並支援 `Ctrl+Enter` 執行 / `Ctrl+Shift+E` 說明計畫。
也可用**自然語言產生 SQL**（✨ 產生 SQL）：依目標 DB 結構由 AI 產生單一 `SELECT` 填入編輯區，
可勾選「自動執行」於產生後立即執行；產生的 SQL 一律經同一道唯讀護欄把關。
基於安全考量僅允許 `SELECT`／`EXPLAIN`（拒絕 DDL／DML／DCL，包含註解繞過與 CTE-DML；連線以 read-only
開啟，statement timeout 30 秒），連線字串僅存於後端、不回傳前端。
若 session 的目標資料庫與平台儲存資料庫（設定頁）為同一個，結構瀏覽器與 NL2SQL 會自動隱藏平台自身的
記帳表（`sessions`／`messages`／`activity_log`／`alembic_version`），避免污染。

| 方法 | 路徑 | 說明 |
|---|---|---|
| `GET` | `/api/settings` | 取得目前記憶後端狀態（密碼遮罩） |
| `POST` | `/api/settings` | 設定／清除作為記憶的資料庫連線（測試連線並建表） |
| `GET` | `/api/activity` | 平台使用紀錄（寫入設定的 PostgreSQL，JSON 模式回空陣列） |
| `GET` | `/api/llm/health` | LLM 連線診斷：實際呼叫 gateway，失敗時回傳完整原因（503） |
| `POST` | `/api/ddl-import` | 由貼上的 CREATE TABLE DDL 建立設計 Session |
| `GET` | `/api/sessions/<id>/schema-tree` | 結構瀏覽器資料（實際 DB 或設計 Schema） |
| `POST` | `/api/sessions/<id>/query` | 對 Session 的目標資料庫執行唯讀 SQL |
| `POST` | `/api/sessions/<id>/explain` | 回傳查詢的 `EXPLAIN` 計畫 |
| `POST` | `/api/sessions/<id>/nl2sql` | 由自然語言問題產生唯讀 `SELECT`（不執行）|
| `POST` | `/api/sessions/<id>/validate-ddl` | 將 `03_ddl.sql` 在暫存 schema 內 dry-run（交易回滾）驗證 |

### CLI 工具

```bash
python main.py
```

對話流程與確認方式同舊版。確認詞：`OK` / `確認` / `yes` / `confirm` / `好` / `可以` / `沒問題`

---

## 輸出文件

| 檔案 | 模式 | 說明 |
|---|---|---|
| `01_specification.md` | 設計 | 資料庫規格書與資料字典（欄位表格） |
| `02_er_diagram.md` | 設計 | Mermaid ER Diagram |
| `03_ddl.sql` | 設計 | PostgreSQL DDL + 索引 + Migration + Seed Data |
| `04_security_plan.md` | 設計 | 索引策略、存取控制、敏感欄位加密建議 |
| `05_review_report.md` | 審查 | AI 審查報告（設計一致性 / 資料完整性 / 效能 / 安全）|
| `06_review_fix.sql` | 審查 | 規則式紅旗自動產生的修復腳本（可套用 ALTER + 需人工判斷的 TODO）|

設計模式另可在文件頁**按需產生**以下延伸檔案（核心 4 份以外）：

| 檔案 | 說明 | 成本 |
|---|---|---|
| `05_orm_models.py` | SQLAlchemy 2.0 ORM 模型 | 1 次 API |
| `06_migration.py` | 全量 Alembic migration | 1 次 API |
| `07_queries.sql` | 常用查詢範例 | 1 次 API |
| `08_incremental_migration.sql` | 現有 DB → 設計的增量 ALTER（需已匯入現有 DB）| 1 次 API |
| `09_schema.dbml` | dbdiagram.io DBML | 純模板 |
| `10_schema.puml` | PlantUML ER 圖 | 純模板 |
| `11_json_schema.json` | JSON Schema（draft-07）| 純模板 |
| `12_data_dictionary.csv` | 資料字典 CSV | 純模板 |

網頁平台：在文件頁直接預覽（Mermaid 渲染、SQL 語法高亮），或下載單檔 / 全部打包為 `.zip`。
CLI 工具：輸出至 `output/{YYYYMMDD_HHMMSS}/`。

---

## 測試

```bash
# 執行所有單元測試（不需要 API 連線）
pytest tests/ -v
```

---

## 專案結構

```
SQL_agent/
├── app.py                       # 網頁平台入口（Flask app factory + blueprint 註冊，本身不含路由）
├── main.py                      # CLI 入口
├── requirements.txt
├── .env.example
│
├── web/                         # 網頁平台後端邏輯
│   ├── session_store.py         # Session 持久化（PostgreSQL / JSON 雙模式）+ 版本管理
│   ├── app_settings.py          # 平台設定（記憶用 DB 連線字串）持久化
│   ├── activity_log.py          # 平台使用紀錄寫入 PostgreSQL（best-effort）
│   ├── db_engine.py             # SQLAlchemy engine 單例 + is_pg_mode() 切換
│   ├── db_schema.py             # SQLAlchemy Core 資料表定義（sessions / messages / activity_log）
│   ├── db_manager.py            # 資料庫管理 Agent：execute_query / explain / schema_tree（唯讀）
│   ├── ddl_parser.py            # CREATE TABLE DDL → TableSpec 解析器
│   ├── schema_advisor.py        # 規則式設計顧問（確認頁警告）
│   ├── generation_worker.py     # 背景 Thread：文件產出（並行）/ 審查
│   ├── db_introspect.py         # PostgreSQL 結構擷取 + 格式化
│   ├── schema_diff.py           # 設計 Schema vs 現有 DB 差異比對
│   ├── convention_checker.py    # 建表標準一致性檢查（多數決推斷命名/PK/時間欄位慣例）
│   ├── table_relation.py        # 需求與現有資料表關聯分析（相關表 / FK 建議 / 重複風險）
│   ├── metadata_checker.py      # 既有資料表說明完整性統計 + COMMENT ON DDL 草擬
│   ├── sql_safety.py            # 統一 SQL 安全層（唯讀護欄 + DDL allowlist，唯一語句切分邏輯）
│   ├── ddl_validator.py         # DDL dry-run 驗證（暫存 schema + 交易回滾）
│   ├── ddl_executor.py          # DDL 交易式執行（單一交易，全成功才 commit）
│   ├── change_requests.py       # 變更請求持久化（PostgreSQL / JSON 雙模式，人工審批用）
│   ├── response_utils.py        # 共用 response 處理（遮蔽連線字串、隱藏平台表、清理錯誤訊息）
│   └── routes/
│       ├── pages.py             # 6 個 HTML 頁面路由
│       ├── sessions.py          # Session CRUD、訊息、confirm/continue、版本、outputs/zip
│       ├── workbench.py         # DDL 匯入、SQL 工作台（query/explain/nl2sql/schema-tree/validate-ddl）
│       ├── settings.py          # /api/settings、/api/settings/business-db、/api/activity
│       ├── agent.py             # DB Agent blueprint：/api/db-agent/* 路由
│       └── changes.py           # 變更請求審批 blueprint：/api/change-requests/*（ADMIN_TOKEN 保護 approve/reject）
│
├── alembic/                     # PostgreSQL migration（alembic upgrade head）
│   ├── env.py
│   └── versions/
│       ├── 0001_initial.py      # sessions / messages
│       ├── 0002_activity_log.py # 平台使用紀錄表
│       ├── 0003_memory_synced.py # sessions.memory_synced 旗標
│       └── 0004_change_requests.py # change_requests 表
│
├── templates/                   # Jinja2 HTML 模板
│   ├── base.html
│   ├── index.html               # 首頁（專案列表 + 模式選擇）
│   ├── chat.html                # 對話頁
│   ├── confirm.html             # 需求確認頁（含 Diff + 版本歷史）
│   ├── docs.html                # 文件查閱頁
│   ├── review.html              # 審查報告頁
│   └── settings.html            # 設定頁（記憶用 DB 連線）
│
├── static/
│   ├── css/main.css             # 設計系統（色板、排版、組件）
│   └── js/
│       ├── home.js
│       ├── chat.js
│       ├── confirm.js
│       ├── docs.js
│       ├── review.js
│       └── settings.js
│
├── agents/
│   ├── orchestrator.py          # CLI 狀態機
│   ├── interviewer.py           # 需求收集 Agent（回傳 3-tuple + REQUIREMENTS_SUMMARY）
│   ├── reviewer.py              # 現有 DB 審查 Agent
│   ├── agent_loop.py            # DB Agent 的 ReAct 工具迴圈（run_agent_turn，取代舊版單發式 db_agent.py）
│   ├── tool_registry.py         # DB Agent 可呼叫的工具目錄（list_databases / get_schema / run_query / propose_ddl / ...）
│   └── writers/
│       ├── spec_writer.py       # 規格書（模板渲染，不耗 API）
│       ├── diagram_writer.py    # ER Diagram（Mermaid）
│       ├── ddl_writer.py        # PostgreSQL DDL + migration
│       ├── security_writer.py   # 效能與安全規劃
│       └── （on-demand）orm_writer / migration_writer / query_writer /
│           incremental_migration_writer / dbml / plantuml / json_schema / data_dict_writer
│
├── models/
│   ├── schema.py                # ColumnSpec, TableSpec
│   └── session.py               # CLI 狀態機 Phase enum
│
├── prompts/
│   ├── interviewer.txt
│   ├── reviewer.txt
│   ├── writers.txt
│   └── agent_loop.txt           # DB Agent 系統提示（工具協定 + few-shot 連鎖範例）
│
├── utils/
│   ├── client.py                # LLMClient（OpenAI 相容 Chat Completions）HTTP 封裝
│   └── file_writer.py           # CLI 輸出目錄管理
│
├── tests/
│   ├── fixtures/sample_spec.json
│   └── test_*.py
│
├── data/                        # Session 資料（git ignored）
└── output/                      # CLI 產出目錄（git ignored）
```

---

## 架構說明

詳見 [docs/architecture.md](docs/architecture.md)
