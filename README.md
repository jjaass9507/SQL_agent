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

### 其他功能

- **設計版本管理**：每次修改後自動快照，可從確認頁一鍵還原任意版本
- **Schema Diff**：確認頁自動比對設計 Schema 與現有 DB 結構，列出新增 / 刪除 / 變更欄位
- **現有 DB 匯入**：可匯入 PostgreSQL 結構作為設計參考（支援 10 / 30 / 30+ 張表三段精簡模式）
- **On-demand 延伸產出**：文件頁可按需產生 ORM 模型、Alembic migration、查詢範例；若有匯入現有 DB，另可產生 **增量 Migration（現有 DB → 設計的 ALTER 腳本）**
- **匯出格式**：一鍵匯出 DBML（dbdiagram.io）、PlantUML ER 圖、JSON Schema、資料字典 CSV（純模板、零 API 成本）
- **DDL Dry-run 驗證**：在文件頁 DDL 分頁按「驗證 DDL」，把 `03_ddl.sql` 套用到暫存 schema 並回滾，回報語法/相依錯誤（需 session 的資料庫或設定頁的平台 PostgreSQL）

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
| `LLM_BASE_URL` | ✓ | OpenAI 相容 Chat Completions API 端點（`{base_url}/chat/completions`） |
| `LLM_API_KEY` | ✓ | API 金鑰（僅放 `.env`，不得寫入任何程式碼或文件） |
| `LLM_MODEL` | ✓ | 模型名稱 |
| `LLM_VERIFY` | | SSL 憑證驗證（預設 `false`，供自簽憑證端點使用） |
| `DATABASE_URL` | | 設定後 Session 改存 PostgreSQL；未設定則用 `data/*.json` |
| `DATA_DIR` | | JSON 模式的資料目錄（預設 `data/`） |

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
├── app.py                       # 網頁平台入口（Flask）
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
│   └── schema_diff.py           # 設計 Schema vs 現有 DB 差異比對
│
├── alembic/                     # PostgreSQL migration（alembic upgrade head）
│   ├── env.py
│   └── versions/
│       ├── 0001_initial.py      # sessions / messages
│       ├── 0002_activity_log.py # 平台使用紀錄表
│       └── 0003_memory_synced.py # sessions.memory_synced 旗標
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
│   └── writers.txt
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
