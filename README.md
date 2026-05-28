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
```

### 其他功能

- **設計版本管理**：每次修改後自動快照，可從確認頁一鍵還原任意版本
- **Schema Diff**：確認頁自動比對設計 Schema 與現有 DB 結構，列出新增 / 刪除 / 變更欄位
- **現有 DB 匯入**：可匯入 PostgreSQL 結構作為設計參考（支援 10 / 30 / 30+ 張表三段精簡模式）
- **系統事件日誌**：關鍵操作寫入 `logs/system.log.jsonl`，敏感欄位會自動遮蔽

---

## 環境需求

- Python 3.11+
- Pensieve API 帳號（`token` + `empno`）

---

## 安裝

```bash
git clone <repo-url>
cd SQL_agent

pip install -r requirements.txt

cp .env.example .env
# 編輯 .env，填入你的 Pensieve 帳號資訊
```

---

## 設定（`.env`）

```env
PENSIEVE_TOKEN=your_token_here
PENSIEVE_EMPNO=your_empno_here
PENSIEVE_URL=https://pensieve.kh.asegroup.com/api/flow_chat/
PENSIEVE_BUILDING=question
PENSIEVE_VERIFY=false
```

| 變數 | 必填 | 說明 |
|---|---|---|
| `PENSIEVE_TOKEN` | ✓ | Pensieve API token |
| `PENSIEVE_EMPNO` | ✓ | 員工編號 |
| `PENSIEVE_URL` | | API 端點（預設官方網址） |
| `PENSIEVE_BUILDING` | | 使用的 flow 名稱（預設 `question`） |
| `PENSIEVE_VERIFY` | | SSL 憑證驗證（預設 `false`） |
| `SQL_AGENT_LOG_DIR` | | 系統事件日誌目錄（預設 `logs/`） |

---

## 使用方式

### 網頁平台（建議）

```bash
python app.py
# 開啟瀏覽器 http://localhost:5000
```

**設計模式**（預設）：首頁（專案管理）→ 對話頁（需求收集）→ 確認頁（Schema 審閱 + Diff + 版本管理）→ 文件頁（即時進度 + 預覽/下載）。

**審查模式**：首頁（選「🔍 審查模式」並填入 DB 連線字串）→ 審查頁（AI 自動分析並輸出報告）。

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
│   ├── session_store.py         # Session JSON 持久化 + threading.Lock + 版本管理
│   ├── generation_worker.py     # 背景 Thread：文件產出（並行）/ 審查
│   ├── db_introspect.py         # PostgreSQL 結構擷取 + 格式化
│   ├── schema_diff.py           # 設計 Schema vs 現有 DB 差異比對
│   └── system_log.py            # 系統事件 JSONL log
│
├── templates/                   # Jinja2 HTML 模板
│   ├── base.html
│   ├── index.html               # 首頁（專案列表 + 模式選擇）
│   ├── chat.html                # 對話頁
│   ├── confirm.html             # 需求確認頁（含 Diff + 版本歷史）
│   ├── docs.html                # 文件查閱頁
│   └── review.html              # 審查報告頁
│
├── static/
│   ├── css/main.css             # 設計系統（色板、排版、組件）
│   └── js/
│       ├── home.js
│       ├── chat.js
│       ├── confirm.js
│       ├── docs.js
│       └── review.js
│
├── agents/
│   ├── orchestrator.py          # CLI 狀態機
│   ├── interviewer.py           # 需求收集 Agent（回傳 3-tuple + REQUIREMENTS_SUMMARY）
│   ├── reviewer.py              # 現有 DB 審查 Agent
│   └── writers/
│       ├── spec_writer.py       # 規格書（模板渲染，不耗 API）
│       ├── diagram_writer.py    # ER Diagram（Mermaid）
│       ├── ddl_writer.py        # PostgreSQL DDL + migration
│       └── security_writer.py   # 效能與安全規劃
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
│   ├── client.py                # PensieveAPI HTTP 封裝
│   └── file_writer.py           # CLI 輸出目錄管理
│
├── docs/
│   ├── architecture.md
│   ├── platform_design_spec.md
│   ├── architecture_completeness_review.md
│   └── operations_runbook.md
│
├── tests/
│   ├── fixtures/sample_spec.json
│   └── test_*.py
│
├── data/                        # Session 資料（git ignored）
├── logs/                        # 系統事件日誌（git ignored）
└── output/                      # CLI 產出目錄（git ignored）
```

---

## 架構說明

- [系統架構說明](docs/architecture.md)
- [平台設計規格書](docs/platform_design_spec.md)
- [架構完整度檢核報告](docs/architecture_completeness_review.md)
- [維運 Runbook](docs/operations_runbook.md)
