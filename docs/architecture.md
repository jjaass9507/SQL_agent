# 系統架構說明

## 整體架構

系統提供兩個入口，共用相同的 Agent 核心：

```
使用者 (Browser)                使用者 (Terminal CLI)
        │                               │
        ▼                               ▼
┌───────────────────┐        ┌─────────────────────┐
│   Flask (app.py)  │        │  Orchestrator        │
│   9 REST endpoints│        │  (CLI 狀態機)        │
│   5 HTML pages    │        └──────────┬──────────┘
└────────┬──────────┘                   │
         │                              │
         ▼                              │
┌───────────────────┐                   │
│  session_store    │                   │
│  (JSON in data/)  │                   │
│  threading.Lock   │                   │
└────────┬──────────┘                   │
         │                              │
         └─────────────┬────────────────┘
                       │ 共用核心
                       ▼
        ┌──────────────┴──────────────┐
        │ 需求收集                     │ 文件產生 / 審查
        ▼                             ▼
┌──────────────┐  ┌──────────────────────────────────────┐
│ Interviewer  │  │ SpecWriter     → 01_spec.md           │
│              │  │ DiagramWriter  → 02_diagram.md        │
│ 追問欄位細節  │  │ DDLWriter      → 03_ddl.sql           │
│ XML tag 提取 │  │ SecurityWriter → 04_security.md       │
│ → TableSpec  │  ├──────────────────────────────────────┤
└──────────────┘  │ Reviewer       → 05_review_report.md │
                  │ （僅 review 模式）                    │
                  └──────────────────────────────────────┘
```

---

## 網頁平台架構（`app.py` + `web/`）

### Session 模式與生命週期

網頁平台支援兩種 session 模式，由 `POST /api/sessions` 的 `mode` 欄位決定：

#### 設計模式（`mode: "design"`，預設）

```
POST /api/sessions  (可帶 db_url 匯入現有 DB 作參考)
  → phase="collecting"

POST /api/sessions/{id}/messages  (重複多次)
  → Interviewer.chat() 解析 <TABLE_SPECS> + <REQUIREMENTS_SUMMARY>
  → tables_ready=true → phase="confirming"，寫入 tables[] + key_points[] + table_versions[]

  （每次 set_tables 自動快照版本至 table_versions，最多保留 10 個）

POST /api/sessions/{id}/versions/{n}/restore
  → 原子性還原至第 n 版，phase 重設為 "confirming"

POST /api/sessions/{id}/confirm  （原子性防重複，try_start_generation）
  → phase="generating"，啟動 generation_worker Thread

  Thread 以 ThreadPoolExecutor(max_workers=4) 並行執行：
    SpecWriter / DiagramWriter / DDLWriter / SecurityWriter
    → 各自更新 generation_status[filename] + outputs[filename]
  → 全部完成後 phase="done"

GET /api/sessions/{id}  (前端每 2 秒輪詢)
  → 回傳 generation_status + generation_errors，前端更新進度卡
```

#### 審查模式（`mode: "review"`）

```
POST /api/sessions  (必須帶 db_url)
  → 匯入現有 DB → context_tables[]
  → phase="reviewing"
  → 立即啟動 run_review() 背景 Thread

  Thread：
    Reviewer.review(context_tables) → markdown 報告
  → phase="review_done"，outputs["05_review_report.md"] = 報告

GET /api/sessions/{id}  (前端每 2 秒輪詢)
  → phase="review_done" 時前端渲染報告
```

### Session JSON 結構（`data/{id}.json`）

```json
{
  "id": "uuid",
  "title": "...",
  "created_at": "ISO8601",
  "mode": "design | review",
  "phase": "collecting | confirming | generating | done | reviewing | review_done",
  "messages": [{"role": "user|ai", "content": "..."}],
  "tables": [ ...TableSpec JSON...（設計模式最新版本） ],
  "key_points": ["AI 整合雙方對話的需求摘要"],
  "table_versions": [
    {
      "version": 1,
      "created_at": "ISO8601",
      "tables": [ ...TableSpec JSON... ],
      "key_points": [...]
    }
  ],
  "outputs": {"01_specification.md": "...", ...},
  "generation_status": {"01_specification.md": "waiting|loading|done|failed", ...},
  "generation_errors": {"03_ddl.sql": "錯誤訊息（失敗時才有）"},
  "context_tables": [ ...從現有 DB 匯入的 TableSpec JSON... ],
  "context_text":   "格式化後的現有 DB 結構文字（作為 LLM 記憶/fallback 注入）",
  "memory_synced":  false
}
```

### REST API

| 方法 | 路徑 | 說明 |
|---|---|---|
| `POST` | `/api/sessions` | 建立新 session；可帶 `db_url`、`db_schema`、`mode` |
| `GET` | `/api/sessions` | 列出所有 sessions（含 mode 欄位） |
| `GET` | `/api/sessions/<id>` | 取得 session 狀態（含 generation_status、outputs）|
| `POST` | `/api/sessions/<id>/messages` | 送出訊息，取得 AI 回覆 |
| `POST` | `/api/sessions/<id>/confirm` | 確認需求，啟動背景產出（原子性防重複觸發）|
| `GET` | `/api/sessions/<id>/outputs` | 取得文件內容 |
| `GET` | `/api/sessions/<id>/outputs/zip` | 下載 zip |
| `POST` | `/api/sessions/<id>/import-db` | 對已存在的 session 匯入（或重新匯入）PostgreSQL DB 結構 |
| `GET` | `/api/sessions/<id>/versions` | 列出所有設計版本的摘要（version, created_at, table_count）|
| `POST` | `/api/sessions/<id>/versions/<n>/restore` | 原子性還原至第 n 版，phase 重設為 "confirming" |

### `web/db_introspect.py` — PostgreSQL 結構擷取

負責連線 PostgreSQL 並查詢 `information_schema`：

- `extract_schema(db_url, schema) -> (list[TableSpec], error_str)` — 連線並讀取所有 table/column 的 PK、FK、UNIQUE、INDEX 中繼資料
- `format_context(tables) -> str` — 將 TableSpec 格式化為文字供 Interviewer system prompt 使用；自動依資料表數量分級：
  - ≤10 張：完整欄位
  - 11–30 張：僅 PK/FK/UNIQUE 欄
  - >30 張：超精簡（表名 + 欄位數 + FK 指向）

### `web/schema_diff.py` — Schema 差異比對

- `compute_diff(designed, existing) -> dict` — 比較設計中的 `list[TableSpec]` 與現有 DB 的 `list[TableSpec]`，回傳：
  - `has_changes`: bool
  - `new_tables`: 設計有、DB 無
  - `dropped_tables`: DB 有、設計未包含
  - `modified_tables`: 同名資料表中有欄位增減或型態變更（含 added_columns / removed_columns / changed_columns）
  - `unchanged_tables`: 無差異
- 差異結果由 `confirm_page()` 計算後傳給 `confirm.html` 渲染

### 並發安全

- 每個 session 有一個 `threading.Lock`（由 `web/session_store.py` 管理），確保背景 Thread 更新 JSON 與 Flask request handler 讀取之間不產生競態
- `_interviewer_store` 由模組層級 `threading.Lock` 保護，防止同一 session 建立多個 Interviewer 實例
- `try_start_generation()` 在單一鎖內原子性地將 phase 從 `confirming` 改為 `generating`，防止重複觸發文件產出
- 四個 Writer 以 `ThreadPoolExecutor(max_workers=4)` 並行執行，互相獨立；各自的 `update_generation_status` 呼叫透過 per-session lock 保護

---

## CLI 架構（`main.py` + `agents/orchestrator.py`）

狀態機管理者，不直接呼叫 API。

```
Phase.COLLECTING
  ↓ 收到使用者輸入 → 轉給 Interviewer
  ↓ Interviewer 回傳 TableSpec → 切換至 CONFIRMING

Phase.CONFIRMING
  ↓ 使用者輸入確認詞 → 切換至 GENERATING
  ↓ 使用者輸入其他 → 回到 COLLECTING（繼續修改需求）

Phase.GENERATING
  ↓ 依序呼叫四個 Writer → 寫出檔案
  ↓ 切換至 DONE
```

確認詞集合：`ok`, `確認`, `yes`, `confirm`, `好`, `可以`, `沒問題`

---

## 資料流

```
【設計模式】
使用者文字輸入
    → Interviewer.chat()
        → PensieveAPI.chat(question, answer)
        → 解析 <TABLE_SPECS> + <REQUIREMENTS_SUMMARY> XML tags
    → (reply_text, list[TableSpec], list[str] summary)
        ↓ 並行（ThreadPoolExecutor max_workers=4）
        ├─ SpecWriter.generate(tables)    → str (Markdown)
        ├─ DiagramWriter.generate(tables) → str (Markdown)
        ├─ DDLWriter.generate(tables)     → str (SQL)
        └─ SecurityWriter.generate(tables)→ str (Markdown)
    → outputs["01..04"] + phase="done"

【審查模式】
現有 DB
    → db_introspect.extract_schema()
    → list[TableSpec]  （存為 context_tables）
    → Reviewer.review(tables)
        → PensieveAPI.chat(一次呼叫)
    → str (Markdown 審查報告)
    → outputs["05_review_report.md"] + phase="review_done"
```

---

## Agent 角色

### Interviewer（`agents/interviewer.py`）

- 維護本地 `_history`（`list[dict]`），記錄每輪對話
- **現有 DB 結構作為 LLM 記憶參考**：context_text（現有 DB 結構）不再無條件於第一輪注入，而是**只在對話「動到現有表」時**才提供。判定條件（命中其一即觸發，且具黏性）：
  1. 使用者訊息提及任一現有表名（詞界、不分大小寫）；或
  2. AI 解析出的 `TABLE_SPECS` 有表與現有表同名、或欄位 `references` 指向現有表。
  觸發後呼叫 `PensieveAPI.update_memory(txt)` 將結構寫入 LLM 持久記憶（只上傳一次，由 session `memory_synced` 旗標控管）。在記憶 API 尚未同步成功前，以 system prompt 注入現有結構作為 **fallback**（每個相關回合都注入，直到同步成功）。現有 DB 重新匯入時 `memory_synced` 重置。
- 當需求完整時，LLM 在回覆前附加 `<REQUIREMENTS_SUMMARY>`（3–6 條整合摘要），再附加 `<TABLE_SPECS>` JSON
- 回傳 `(reply_text, list[TableSpec] | None, list[str] summary)`

### Reviewer（`agents/reviewer.py`）

- 單次 API 呼叫，分析匯入的現有 DB 結構
- 回傳四段 Markdown 報告：設計一致性、資料完整性、效能考量、安全性
- 每段 3–5 條建議，格式 `- **資料表名**（欄位名）：問題 → 建議`
- 報告末尾含 `**整體評分：X/10**` 與 2–3 句總評

### Writers（`agents/writers/`）

統一介面：`generate(tables: list[TableSpec]) -> str`

| Writer | API 呼叫 | 說明 |
|---|---|---|
| SpecWriter | **無** | 直接模板渲染，固定格式 Markdown 表格 |
| DiagramWriter | 1 次 | `question`=任務說明，`answer`=TableSpec JSON |
| DDLWriter | 1 次 | `question`=任務說明，`answer`=TableSpec JSON |
| SecurityWriter | 1 次 | `question`=任務說明（含敏感欄位偵測結果），`answer`=TableSpec JSON |

SpecWriter 不呼叫 API 的原因：規格書格式完全確定，從結構化資料直接渲染比 LLM 更快、更穩定、節省費用。

---

## 核心資料模型（`models/schema.py`）

```python
@dataclass
class ColumnSpec:
    name: str           # 欄位名稱
    data_type: str      # PostgreSQL 型態（UUID, VARCHAR, TIMESTAMPTZ...）
    nullable: bool
    description: str
    is_primary_key: bool = False
    is_foreign_key: bool = False
    references: str | None = None   # "other_table.column"
    is_unique: bool = False
    is_indexed: bool = False
    length: int | None = None
    default: str | None = None

@dataclass
class TableSpec:
    table_name: str
    description: str
    columns: list[ColumnSpec]
    constraints: list[str] = []     # CHECK constraints
    related_tables: list[str] = []
```

---

## API 整合（`utils/client.py`）

使用 PensieveAPI 模式，統一的 HTTP 呼叫封裝：

```python
payload = {
    "token": ...,
    "empno": ...,
    "variables": {
        "building": ...,   # flow 名稱，來自 PENSIEVE_BUILDING 環境變數
        "question": ...,   # 任務描述 / system prompt + 上下文
        "answer": ...,     # 當前輸入 / 結構化資料
    }
}
# POST → 解析 response["Result"] → 回傳純文字
```

`get_api()` 提供 singleton，避免重複讀取環境變數。

另提供 `update_memory(content)`：以 multipart 上傳 txt 至 `PENSIEVE_VECTOR_URL`（uploadVector），寫入 `PENSIEVE_VECTOR_ID` 指定的 vector store 作為 LLM 記憶。採固定 filename 達成 coverage（重複上傳取代同一份文件）；解析回傳的 `isSuccess` 與 `SuccessFile` 判定成功。`vector_id` 未設定時回傳 False，呼叫端（Interviewer）退回 system-prompt 注入 fallback。

---

## 輸出格式說明

### `01_specification.md` — 規格書與資料字典

每個資料表產出一個 Markdown 表格（欄位名稱、型態、長度、NULL、預設值、PK/FK/UNIQUE/INDEX 旗標、說明）。

### `02_er_diagram.md` — ER Diagram

包含設計說明文字 + Mermaid 程式碼區塊，網頁平台直接渲染，也可貼到 [mermaid.live](https://mermaid.live) 預覽。

### `03_ddl.sql` — DDL 腳本

依序包含四個區塊：建立腳本（含 COMMENT）、索引建立、Migration 腳本（含回滾）、Seed Data。

### `04_security_plan.md` — 效能與安全規劃書

六個章節：索引策略、查詢效能建議、分區策略、存取控制（含 GRANT 範例）、敏感欄位安全、備份與維運建議。

### `05_review_report.md` — 現有 DB 審查報告（審查模式）

四個段落：設計一致性、資料完整性、效能考量、安全性，各含 3–5 條具體建議。報告末尾含整體評分（X/10）與總評。
