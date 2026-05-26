# SQL Agent — 資料庫建檔管理 Agent 系統

透過對話式 AI，收集資料表設計需求後自動產出四份技術文件：規格書、ER Diagram、DDL 腳本、效能安全規劃。

提供兩種操作介面：
- **網頁平台**（`app.py`）— Flask 網頁介面，含對話、確認、文件檢視頁面
- **CLI 工具**（`main.py`）— 終端機命令列介面

---

## 功能概覽

```
使用者輸入需求
     ↓
Interviewer Agent 追問細節（欄位型態、主鍵、關聯、索引…）
     ↓
使用者確認摘要
     ↓
自動產出四個檔案
```

| 輸出檔案 | 內容 |
|---|---|
| `01_specification.md` | 資料庫規格書與資料字典（欄位表格） |
| `02_er_diagram.md` | Mermaid ER Diagram（可貼到 mermaid.live 預覽） |
| `03_ddl.sql` | PostgreSQL DDL + 索引 + Migration + Seed Data |
| `04_security_plan.md` | 索引策略、存取控制、敏感欄位加密建議 |

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

---

## 使用方式

### 網頁平台（建議）

```bash
python app.py
# 開啟瀏覽器 http://localhost:5000
```

網頁平台提供四個頁面：首頁（專案管理）→ 對話頁（需求收集）→ 確認頁（語意對照審閱）→ 文件頁（即時進度 + 預覽/下載）。

### CLI 工具

```bash
python main.py
```

對話流程與確認方式同舊版。確認詞：`OK` / `確認` / `yes` / `confirm` / `好` / `可以` / `沒問題`

---

## 輸出文件

| 檔案 | 說明 |
|---|---|
| `01_specification.md` | 資料庫規格書與資料字典（欄位表格） |
| `02_er_diagram.md` | Mermaid ER Diagram |
| `03_ddl.sql` | PostgreSQL DDL + 索引 + Migration + Seed Data |
| `04_security_plan.md` | 索引策略、存取控制、敏感欄位加密建議 |

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
│   ├── session_store.py         # Session JSON 持久化 + threading.Lock
│   └── generation_worker.py     # 背景 Thread：文件產出進度管理
│
├── templates/                   # Jinja2 HTML 模板
│   ├── base.html
│   ├── index.html               # 首頁（專案列表）
│   ├── chat.html                # 對話頁
│   ├── confirm.html             # 需求確認頁
│   └── docs.html                # 文件查閱頁
│
├── static/
│   ├── css/main.css             # 設計系統（色板、排版、組件）
│   └── js/
│       ├── home.js
│       ├── chat.js
│       ├── confirm.js
│       └── docs.js
│
├── agents/
│   ├── orchestrator.py          # CLI 狀態機
│   ├── interviewer.py           # 需求收集 Agent
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
│   └── writers.txt
│
├── utils/
│   ├── client.py                # PensieveAPI HTTP 封裝
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
