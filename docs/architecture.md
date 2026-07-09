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
  "context_text":   "格式化後的現有 DB 結構文字（動到現有表時注入 system prompt）",
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

### SQL 安全層（`web/sql_safety.py`）

DB Agent 對業務資料庫下達的唯讀查詢（`db_manager`）與 DDL 變更（`ddl_guard` / `ddl_executor`）共用同一個 SQL 分析模組，避免兩條路徑各自維護語句切分邏輯而產生邊界不一致：

- `skeleton(sql) -> str`：把註解與字串／識別字面值換成**等長空白**（保留 offset），讓關鍵字檢查不會被字面值中的關鍵字誤判，也不會被前導註解繞過；`len(skeleton(sql)) == len(sql)` 恆成立，skeleton 中找到的分號位置可直接用來切原文。
- `split_statements(sql) -> list[str]`：以 `skeleton()` 找出的分號位置切分**原文**（字串/註解內的 `;` 不會被當成語句邊界），回傳去空白後的非空語句清單。
- `check_read_only(sql) -> str | None`：唯讀護欄。先要求 `split_statements(sql)` 剛好一條語句（防止 `SELECT 1; DELETE FROM t` 這類 stacked-statement 繞過），再檢查 SELECT/EXPLAIN 開頭、CTE 內的 DML（`WITH ... DELETE/UPDATE/INSERT/MERGE`）、`SELECT ... INTO`。供 `db_manager._check_sql`（`execute_query`/`explain_query`）委派使用。
- `check_ddl_allowlist(ddl) -> str | None`：DDL allowlist 護欄。檢查長度上限、禁用關鍵字（`DROP`/`TRUNCATE`/`DELETE`/`INSERT`/`UPDATE`/...）、禁止 `ALTER COLUMN`、語句數上限，並逐句比對 allowlist（僅接受 `CREATE TABLE`、`CREATE [UNIQUE] INDEX`、`ALTER TABLE ... ADD COLUMN/CONSTRAINT`）。供 `ddl_guard.check_ddl_safety` 委派使用。

`web/ddl_executor.execute_ddl()` 以 `split_statements()` 切出的語句在**單一交易**內逐句執行：全部成功才 `commit()`，任一語句失敗即 `rollback()` 並回傳錯誤（不再用 `autocommit`，避免中途失敗留下半套 DDL 變更）。

---

## DB Agent — ReAct 工具迴圈（`agents/agent_loop.py` + `agents/tool_registry.py`）

全域對話式助手（`/db-agent` 頁面 + 各頁面右下角的抽屜），取代早期一問一答、進程內單例、歷史無限增長的 `agents/db_agent.py`。核心是一個有界的「LLM ⇄ 工具」迴圈，狀態完全落在 `session_store`，不依賴任何進程內記憶體。

### 資料流

```
POST /api/db-agent/chat {message, db_name}
        │  web/routes/agent.py
        ▼
_get_or_create_agent_session()          單一全域對話，session id 存於 app_settings
        │
        ▼
agents.agent_loop.run_agent_turn(conversation_id, message, db_name)
        │
        ├─ 1. session_store.add_message(role="user")
        ├─ 2. 從 session["messages"] 重建 LLM messages（見下）
        ├─ 3. 迴圈（最多 MAX_STEPS=8 次）：
        │       呼叫 LLMClient.chat_messages(messages, system_prompt)
        │       │
        │       ├─ 回覆含 <TOOL name="...">{json}</TOOL>
        │       │     → tool_registry.dispatch(name, args, ctx)
        │       │     → 結果截斷（每則 ≤20 列 / ≤4,000 字）
        │       │     → 以 assistant(<TOOL>) + user(<OBSERVATION>) 塞回 messages
        │       │     → session_store.add_message(role="tool", content=JSON{tool,args,observation})
        │       │     → 繼續下一輪
        │       │
        │       └─ 回覆含 <FINAL>...</FINAL> 或完全沒有 <TOOL> 標籤
        │             → 從 <FINAL> 內容解析 <DDL_SUGGESTION> / <DESIGN_REQUEST>
        │             → session_store.add_message(role="ai", content=乾淨文字)
        │             → 迴圈結束
        │
        └─ 4. 回傳 {reply, steps:[{tool,args,result_summary,result}], ddl_suggestion, design_request}
        ▼
web/routes/agent.py 把上述結果整形為與舊版相容的 response：
  reply / steps（新）/ ddl_suggestion / ddl_db / query_result / query_error / design_session
```

### 工具目錄（`agents/tool_registry.py`）

`Tool(name, description, args_doc, handler, read_only)`，每個 handler 是既有 `web/` 模組的薄轉接層（~5 行）：

| 工具 | 委派對象 |
|---|---|
| `list_databases` | `app_settings.get_business_databases` |
| `get_schema` | `db_manager.schema_tree` |
| `get_table_ddl` | `db_manager.get_table_ddl` |
| `run_query` | `sql_safety.check_read_only` → `db_manager.execute_query` |
| `explain_query` | `db_manager.explain_query` |
| `analyze_schema` | `db_manager.schema_tree` → `schema_advisor.analyze`（零 API 成本） |
| `check_conventions` | `db_introspect.extract_schema` → `convention_checker.infer_conventions` + `.check_conventions`（零 API 成本） |
| `find_related_tables` | `db_introspect.extract_schema` → `table_relation.find_related`（零 API 成本） |
| `check_table_docs` | `db_introspect.extract_schema` → `metadata_checker.check_metadata_completeness`（零 API 成本） |
| `draft_comment_ddl` | `metadata_checker.draft_comment_ddl`（純文字組裝，零 API 成本） |

`dispatch(name, args, ctx)` 對未知工具、缺參數、handler 內部例外一律回傳 `{"error": ...}` 而不 raise，讓錯誤能當作 observation 回饋給 LLM 自我修正。`ToolContext.resolve_db_url(name)` 依序解析：工具呼叫的 `db` 參數 → 本回合選擇的 `db_name` → 第一個已設定的業務資料庫。`nl2sql` 不在此登錄——agent 自己寫 SQL，`nl2sql` 保留給手動工作台。

`check_conventions` / `find_related_tables` 的 `design_tables` 參數採用與 `dataclasses.asdict(TableSpec)` 相同的 JSON 形狀（`session_store.tables_from_json` 負責還原），由 LLM 在呼叫工具時一併提供正在設計的資料表；未提供 `design_tables` 時 `find_related_tables` 只做需求文字 → 現有表的關鍵詞比對。

### Transcript 重建與截斷（`agents/agent_loop.py`）

- 每回合開始都從 `session_store.get_session(conversation_id)["messages"]` 重新組出 LLM 要看到的完整 messages 陣列——`role="tool"` 的訊息（存成 JSON `{tool, args, observation}`）會展開回原本的 `<TOOL>`/`<OBSERVATION>` 文字配對，因此重啟或換一個 worker 處理下一回合時看到的上下文與原本連續對話完全一致
- Observation 截斷兩層：先把 `rows` 陣列裁到 20 列（附 `truncated: true`），再把整個 JSON 字串裁到 4,000 字元
- messages 總字數超過約 24,000 字時，從最舊的訊息開始丟棄，直到符合預算
- 工具參數 JSON 解析失敗時，錯誤訊息本身當作 observation 回饋給 LLM 重試；連續 2 次解析失敗就中止本回合，避免無限重試迴圈

### 系統提示（`prompts/agent_loop.txt`）

角色/能力/限制與舊版 `db_agent.txt` 相同；額外注入 `tool_registry.render_catalog()` 產生的工具目錄，以及 `_compact_schema_summary()` 產生的精簡結構摘要（僅資料庫與資料表名稱，不含欄位——欄位細節由 `get_schema`/`get_table_ddl` 工具按需查詢，避免每回合都把完整 schema 塞進 prompt）。內含 4 個 few-shot 範例示範 `<TOOL>`→`<OBSERVATION>`→`<FINAL>` 連鎖（含檢查文件完整性 → 草擬 COMMENT → `<DDL_SUGGESTION>` 呈現的流程）。`<DDL_SUGGESTION>`/`<DESIGN_REQUEST>` 標籤語意與舊版相同：前者仍只是文字建議，需使用者按「執行 DDL」才會經 `ddl_guard` 驗證後執行；後者觸發 `web/routes/agent.py` 的 `_create_design_session()` 建立一個新的設計 session。

## 建表標準一致性 / 需求關聯分析 / 文件完整性檢查

三個純規則式模組（零 API 成本），供確認頁、Interviewer、DB Agent 工具共用：

| 模組 | 函式 | 用途 |
|---|---|---|
| `web/convention_checker.py` | `infer_conventions(existing_tables) -> dict` | 從現有 DB 的 `TableSpec` 清單以多數決推斷命名風格（snake/camel）、PK 慣例（欄名/型態）、`created_at` 比例、FK `_id` 命名比例、軟刪除比例；資料表數 < 3 時回傳 `{}`（樣本不足不檢查） |
| | `check_conventions(design_tables, conventions) -> list[dict]` | 逐表比對，回傳與 `schema_advisor.analyze()` 同形的警告（`{level, code, table, column, message}`），code 如 `convention_naming` / `convention_pk_type` / `convention_timestamps` / `convention_fk_naming` / `convention_soft_delete` |
| `web/table_relation.py` | `find_related(requirement_text, design_tables, existing_tables) -> dict` | 確定性計分：需求文字關鍵詞命中現有表名/欄位/註解 → `related`；設計表的 `xxx_id` 欄位對映現有表 PK → `fk_suggestions`；欄位重疊率 > 60% → `duplicate_risks` |
| `web/metadata_checker.py` | `check_metadata_completeness(existing_tables) -> dict` | 統計 table/column comment 覆蓋率與缺漏清單，排除平台記帳表（沿用 `db_schema.platform_table_names()`） |
| | `draft_comment_ddl(db, table, comments) -> str` | 把 `{table_comment, columns: {name: comment}}` 組成安全引用（雙引號 identifier、單引號跳脫）的 `COMMENT ON TABLE/COLUMN` 語句 |

**整合點**：
- **DB Agent 工具**：`tool_registry` 註冊 `check_conventions` / `find_related_tables` / `check_table_docs` / `draft_comment_ddl`，資料來源一律用 `db_introspect.extract_schema()`（含 table/column comment，`get_schema` 用的 `db_manager.schema_tree` 沒有）；`design_tables` 參數與 `dataclasses.asdict(TableSpec)` 同形
- **`web/sql_safety.py`**：`check_ddl_allowlist` 的 allowlist 加入 `COMMENT ON (TABLE|COLUMN)`——`COMMENT ON ... IS '...'` 的字串內容會被 `skeleton()` 遮蔽，不影響關鍵字檢查
- **確認頁**（`app.py:confirm_page`）：有 `context_tables`（匯入的現有 DB）時，把 `check_conventions` 的警告併入 `schema_advisor` 的警告清單一併顯示；`find_related()` 的結果（以 `session.key_points` 串接作為需求文字）放進新欄位 `relation_report`，`templates/confirm.html` 以 `RELATION_REPORT` JSON 傳給 `static/js/confirm.js` 動態渲染「與現有資料庫的關聯」區塊（相關表 / FK 建議 / 重複風險，無資料時不顯示）
- **Interviewer**（`agents/interviewer.py`）：建構時若有 ≥ 3 張現有表（`existing_table_specs`）便推斷 conventions；設計階段的每一輪（既有-DB 結構被觸發注入時）額外注入 conventions 摘要與該輪訊息命中的 top 3 相關表，引導 LLM 遵循現有標準、參照現有表
- **DB Agent 頁**：`db_agent.js` 的「📋 檢查文件完整性」按鈕送出固定訊息，觸發 agent 走 `check_table_docs` → 草擬說明 → `draft_comment_ddl` → `<DDL_SUGGESTION>` 流程（見 `prompts/agent_loop.txt` 的「文件完整性檢查流程」段落）

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
        → LLMClient.chat(question, answer)
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
        → LLMClient.chat(一次呼叫)
    → str (Markdown 審查報告)
    → outputs["05_review_report.md"] + phase="review_done"
```

---

## Agent 角色

### Interviewer（`agents/interviewer.py`）

- 維護本地 `_history`（`list[dict]`），記錄每輪對話
- **現有 DB 結構注入 system prompt**：Chat Completions API 無跨請求記憶機制，因此改為對話「動到現有表」時（使用者提及現有表名，或 `TABLE_SPECS` 同名/外鍵指向現有表）即把現有 DB 結構文字（`context_text`）注入 system prompt；一旦觸發即持續注入（sticky）。
- `sessions.memory_synced` 欄位保留於資料庫（供未來擴充），但 Interviewer 不再讀寫此旗標，一律以上述規則注入。
- 當需求完整時，LLM 在回覆前附加 `<REQUIREMENTS_SUMMARY>`（3–6 條整合摘要），再附加 `<TABLE_SPECS>` JSON
- 回傳 `(reply_text, list[TableSpec] | None, list[str] summary)`

### Reviewer（`agents/reviewer.py`）

- 單次 API 呼叫，分析匯入的現有 DB 結構
- 回傳四段 Markdown 報告：設計一致性、資料完整性、效能考量、安全性
- 每段 3–5 條建議，格式 `- **資料表名**（欄位名）：問題 → 建議`
- 報告末尾含 `**整體評分：X/10**` 與 2–3 句總評

審查模式除 AI 報告外，`_review` 另以**純規則**（不耗 API）產出兩項：
- `web/schema_advisor.py:analyze()` 的紅旗清單（每條含 `code`），存入 `session["review_warnings"]`，審查頁分「警告/建議」渲染。
- `web/schema_remediation.py:build_remediation_sql()` 依紅旗 `code` 產生修復 `06_review_fix.sql`：可直接套用者（FK 索引、UNIQUE、timestamptz、稽核欄位）給出 SQL，需人工判斷者（缺 PK、varchar 長度、enum CHECK、敏感欄位）以 `-- TODO` 註解列出。

### Writers（`agents/writers/`）

統一介面：`generate(tables: list[TableSpec]) -> str`

| Writer | API 呼叫 | 說明 |
|---|---|---|
| SpecWriter | **無** | 直接模板渲染，固定格式 Markdown 表格 |
| DiagramWriter | 1 次 | Mermaid `erDiagram` 由 `build_mermaid_er(tables)` **確定性產生**（保證合法語法、型態去括號）；LLM 僅生成關聯說明文字 |
| DDLWriter | 1 次 | `question`=任務說明，`answer`=TableSpec JSON |
| SecurityWriter | 1 次 | `question`=任務說明（含敏感欄位偵測結果），`answer`=TableSpec JSON |

SpecWriter 不呼叫 API 的原因：規格書格式完全確定，從結構化資料直接渲染比 LLM 更快、更穩定、節省費用。

#### On-demand 延伸產出（`POST /api/sessions/<id>/extras/<kind>/generate`）

非核心 4 文件，由使用者在文件頁按需產生（`web/generation_worker.py:EXTRA_FILES`）：

| kind | 輸出檔 | Writer | API | 說明 |
|---|---|---|---|---|
| `orm` | `05_orm_models.py` | ORMWriter | 1 次 | SQLAlchemy 2.0 模型 |
| `migration` | `06_migration.py` | MigrationWriter | 1 次 | 全量 Alembic migration |
| `query` | `07_queries.sql` | QueryWriter | 1 次 | 常用查詢範例 |
| `incremental` | `08_incremental_migration.sql` | IncrementalMigrationWriter | 1 次 | **現有 DB → 設計** 的增量 ALTER migration |
| `dbml` | `09_schema.dbml` | DBMLWriter | **無** | dbdiagram.io DBML |
| `plantuml` | `10_schema.puml` | PlantUMLWriter | **無** | PlantUML ER 圖 |
| `jsonschema` | `11_json_schema.json` | JSONSchemaWriter | **無** | JSON Schema（draft-07）|
| `datadict` | `12_data_dictionary.csv` | DataDictWriter | **無** | 資料字典 CSV |

確認後的核心產出（`_generate`）只跑 `GENERATION_FILES`（01–04）；其餘皆為 on-demand，由 `run_single_file()` → `_run_one()` → `_make_writer()`（查 `_WRITER_MAP`）個別產生。

`incremental` 例外：需同時有設計結構與已匯入的現有 DB（`context_tables`），故**不在** `_WRITER_MAP`，改走專屬 `run_incremental()`——以 `web/schema_diff.py:compute_diff` 算差異後交 LLM 產生 ALTER/CREATE（破壞性 DROP 以註解提供）+ 回滾；無現有 DB 或無差異時短路、不呼叫 API。前端僅在有匯入現有 DB 時顯示此按鈕。

`09–12` 為純模板匯出（由 `TableSpec` 直接渲染，零 API 成本、瞬間完成）。

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

`LLMClient` 呼叫 OpenAI 相容 Chat Completions API，統一的 HTTP 呼叫封裝：

```python
POST {LLM_BASE_URL}/chat/completions
Authorization: Bearer {LLM_API_KEY}

payload = {
    "model": ...,          # 來自 LLM_MODEL 環境變數
    "messages": [
        {"role": "system", "content": [{"type": "text", "text": ...}]},  # system_prompt 有值時插入
        {"role": "user",   "content": [{"type": "text", "text": ...}]},
    ],
}
# POST → 解析 choices[0].message.content（字串或 parts 陣列皆支援）→ 回傳純文字
```

核心方法 `chat_messages(messages, system_prompt=None)`；相容方法 `chat(system_prompt, human_prompt)` 包成單則 user 訊息呼叫 `chat_messages`，供既有 Interviewer/Reviewer/Writers/nl2sql 呼叫端零改動沿用。

HTTP 429（rate limit）自動指數退避重試（2/4/8 秒，最多 3 次）；requests 例外時記錄 log 並回傳 `None`。

`get_api()` 提供 singleton，避免重複讀取環境變數；缺少 `LLM_BASE_URL`／`LLM_API_KEY`／`LLM_MODEL` 任一環境變數時會 raise 清楚錯誤。金鑰只存在 `.env`（git ignored），不得寫入任何程式碼或文件。

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
