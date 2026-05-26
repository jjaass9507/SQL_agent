# SQL Agent — 資料庫建檔管理 Agent 系統

透過對話式 AI，收集資料表設計需求後自動產出四份技術文件：規格書、ER Diagram、DDL 腳本、效能安全規劃。

---

## 功能概覽

```
使用者輸入需求
     ↓
Interviewer Agent 追問細節（欄位型態、主鍵、關聯、索引…）
     ↓
使用者確認摘要（輸入 OK）
     ↓
自動產出四個檔案至 output/{timestamp}/
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

```bash
python main.py
```

### 對話流程

```
資料庫建檔管理 Agent
請描述您想建立的資料表需求...

您> 我想建立一個訂單系統，包含訂單主表和訂單明細

Agent> 好的，請問訂單主表的主鍵設計是用自增 ID 還是 UUID？
       另外，訂單與哪些表有關聯（例如 users）？

您> UUID 主鍵，關聯 users 表，需要訂單狀態欄位

Agent> 了解！還需確認幾點：
       1. 訂單狀態有哪些值？（pending/paid/shipped/cancelled？）
       2. 是否需要 created_at / updated_at 時間戳？

您> 狀態用那四個值，要時間戳

Agent> [顯示完整需求摘要表格]

需求已收集完整！輸入 OK 開始產生文件，或說明需要修改的地方。

您> OK

  ✓ 規格書與資料字典  (01_specification.md)
  ✓ 結構與關聯圖      (02_er_diagram.md)
  ✓ DDL 腳本          (03_ddl.sql)
  ✓ 效能與安全規劃書  (04_security_plan.md)

文件產生完成！→ output/20260526_143022/
```

### 支援的確認指令

在摘要確認階段，可輸入以下任一詞語繼續：
`OK` / `確認` / `yes` / `confirm` / `好` / `可以` / `沒問題`

輸入其他任何內容則視為修改需求，繼續對話收集。

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
├── main.py                      # CLI 入口
├── requirements.txt
├── .env.example
│
├── agents/
│   ├── orchestrator.py          # 狀態機（COLLECTING→CONFIRMING→GENERATING）
│   ├── interviewer.py           # 需求收集 Agent
│   └── writers/
│       ├── spec_writer.py       # 規格書（模板渲染，不耗 API）
│       ├── diagram_writer.py    # ER Diagram（Mermaid）
│       ├── ddl_writer.py        # PostgreSQL DDL + migration
│       └── security_writer.py  # 效能與安全規劃
│
├── models/
│   ├── schema.py                # ColumnSpec, TableSpec
│   └── session.py               # 對話狀態機 Phase enum
│
├── prompts/
│   ├── interviewer.txt          # Interviewer system prompt
│   └── writers.txt              # Writer agents 共用 prompt
│
├── utils/
│   ├── client.py                # PensieveAPI（HTTP 呼叫封裝）
│   └── file_writer.py           # 輸出目錄與檔案管理
│
├── tests/
│   ├── fixtures/sample_spec.json
│   ├── test_models.py
│   └── test_writers.py
│
└── output/                      # 產出目錄（git ignored）
    └── {YYYYMMDD_HHMMSS}/
        ├── 01_specification.md
        ├── 02_er_diagram.md
        ├── 03_ddl.sql
        └── 04_security_plan.md
```

---

## 架構說明

詳見 [docs/architecture.md](docs/architecture.md)
