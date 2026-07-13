# SQL Agent v2 — 全面重建架構計畫書

> **文件用途**：本文件是 SQL Agent 整個專案「打掉重練」的完整規劃，
> 供後續交由 AI 工程師（Claude Sonnet 5）分階段實作。每個階段都有明確的
> 交付物與驗證標準，可獨立驗收。
>
> **需求來源**：`docs/project_charter.md`、`docs/requirements_spec.md`、
> `docs/user_stories.md`、`docs/security_design.md`、`docs/db_schema.md`
> 的主要需求全數保留；本計畫改變的是**實作方式**，不是產品需求。
>
> **實作紀律**：實作時遵循根目錄 `CLAUDE.md` 與 `DEVELOPMENT_GUIDELINES.md`
> （簡單優先、不做規格外功能、每階段有可驗證的成功標準、文件與程式同步）。

---

## 一、為什麼重建（現況問題診斷）

v0.5 的功能已完整，但底層作法累積了大量「繞路」的設計，維護成本高：

| # | 現況問題 | 影響 | v2 作法 |
|---|---|---|---|
| 1 | **手刻 `requests` HTTP 客戶端**（`utils/client.py`），自行處理 retry、timeout、content 形態 | 400 行程式碼做 SDK 一行能做的事；bug 多（URL 重複組合、SSL verify 等都實際踩過） | 改用官方 `openai` Python SDK（支援自訂 `base_url`，任何 OpenAI 相容 gateway 皆可用） |
| 2 | **自製 `<TOOL>`/`<OBSERVATION>`/`<FINAL>` XML 標籤協定**做 agent 工具迴圈 | 解析脆弱、prompt 冗長、模型容易格式跑掉 | 改用 Chat Completions **原生 function calling**（`tools` 參數 + `tool_calls` + `role:"tool"`） |
| 3 | **自製 `<TABLE_SPECS>`/`<REQUIREMENTS_SUMMARY>` XML 標籤**抽取結構化 Schema | 同上；欄位缺漏需大量 `.get()` 防禦 | 改用 **structured outputs**（`response_format: json_schema`），由 Pydantic 模型直接定義與驗證 |
| 4 | **多輪對話相容性補丁層層疊加**（`LLM_SYSTEM_MODE` 三階梯、`LLM_CONTENT_FORMAT` 雙形態、single_turn 攤平） | 設定矩陣複雜、使用者需手動診斷後改 `.env` 重啟 | 標準多輪為唯一預設路徑；gateway 相容性收斂成**單一自動偵測的能力檔（CapabilityProfile）+ 降級轉接層**（見第四章） |
| 5 | Flask 同步框架 + 手寫 Pydantic 文件（未接入路由） | NFR-04 要求 OpenAPI 3.0 文件，目前靠手寫 | 改用 **FastAPI**：Pydantic 原生驗證 + OpenAPI 自動生成 + async |
| 6 | JSON 檔案 / PostgreSQL 雙後端 session store | 兩套邏輯要同步維護；JSON 模式無交易 | **單一 SQLAlchemy 2.0 後端**：正式用 PostgreSQL、本機開發與測試用 SQLite（同一套 code） |
| 7 | 前端每 2 秒輪詢 | 延遲感高、無效請求多 | **SSE**（Server-Sent Events）推播生成進度與對話串流 |
| 8 | 生成 job 跑在 Flask process 的 Thread 裡 | 重啟即中斷、無法追蹤 | **DB-backed job 表** + 程序內 asyncio worker（介面預留可換成獨立 worker/queue） |
| 9 | 無認證 | 持 UUID 即可讀寫任意 session | JWT 認證 + session 所有權驗證（獨立階段實作，介面從第一天預留 `user_id`） |
| 10 | CSS/JS 與頁面結構耦合 | 未來換設計風格需大改 | **Design token + 元件庫**分層，為 Google Stitch 設計置換預留介面（見第九章） |

---

## 二、保留的產品需求（主要需求清單）

以下功能是 v2 的驗收範圍，需求細節以既有文件為準：

1. **設計模式**：自然語言多輪需求收集 → Schema 確認頁（摘要 + 表格 + Diff + 版本管理）→ 並行產出四份核心文件（規格書 / Mermaid ER 圖 / PostgreSQL DDL / 效能安全規劃）（FR-01～FR-05）
2. **審查模式**：匯入現有 PostgreSQL → AI 審查報告（四維度 + 評分）+ 規則式紅旗 + 修復 SQL（FR-06）
3. **DDL 匯入**：貼上 CREATE TABLE 直接進確認頁
4. **DB Agent**：全域對話助手，多步驟工具推理（查 schema、跑唯讀查詢、檢查慣例/文件完整性、草擬 COMMENT、提案 DDL）
5. **人工審批（HITL）**：所有結構變更走 change request，管理員核准後才在單一交易內執行
6. **SQL 工作台**：結構瀏覽器 + 唯讀查詢/EXPLAIN + NL2SQL
7. **On-demand 延伸產出**：ORM 模型、Alembic migration、查詢範例、增量 migration、DBML/PlantUML/JSON Schema/CSV 純模板匯出
8. **Session 管理**：列表、篩選、狀態、版本快照（最多 10 版）
9. **非功能需求**：非 AI API P95 < 200ms、四文件並行產出 P95 < 60s、100 並發 session、結構化 JSON log、OpenAPI 3.0 文件（NFR-01～05）

**明確不做**（沿用 charter 的 out of scope）：多人協作、手機版 RWD、分享連結、Word/PDF 匯出、多語介面。
**v2 額外裁掉**（已與使用者確認）：CLI 入口（`main.py`）移除——網頁平台是唯一介面；若日後需要，以呼叫同一組 service 層的薄殼補回。

---

## 三、v2 技術選型與整體架構

### 3-1 技術棧

| 層 | 選型 | 理由 |
|---|---|---|
| Web 框架 | **FastAPI** + uvicorn | async 原生、Pydantic 請求驗證、OpenAPI 自動生成（直接滿足 NFR-04）、SSE 容易 |
| LLM 客戶端 | **官方 `openai` SDK**（`AsyncOpenAI`） | `base_url` 可指向任何相容 gateway；內建 timeout/retry/streaming；砍掉整個手刻 HTTP 層 |
| ORM / DB | **SQLAlchemy 2.0**（async）+ Alembic；正式 PostgreSQL 14+、開發/測試 SQLite | 單一後端程式碼；避免 PG 專屬型態（陣列改 JSON 欄位）讓 SQLite 可跑測試 |
| 背景工作 | DB-backed `jobs` 表 + 程序內 asyncio worker | 單一部署單元即可滿足規模需求；`JobRunner` 介面預留換成獨立 worker |
| 推播 | SSE（`text/event-stream`） | 比 WebSocket 簡單、單向推播已足夠 |
| 前端 | Jinja2 templates + 原生 ES modules + **design token CSS** | 內部工具規模不需要 SPA 框架；token 分層是 Stitch 置換的前提 |
| 驗證/設定 | Pydantic v2 + `pydantic-settings`（取代散落的 `os.environ` 讀取） | 啟動即驗證所有環境變數，錯誤訊息集中清楚 |
| 測試 | pytest + pytest-asyncio + respx（mock LLM HTTP） | 所有測試不需真實 API |
| Lint/格式 | ruff（lint + format） | 單一工具 |

### 3-2 分層架構

```
Browser ──HTTP/SSE──► FastAPI (api/) 
                          │  Pydantic request/response schemas
                          ▼
                      services/          業務邏輯（純 Python，不碰 HTTP）
                          │
              ┌───────────┼──────────────┐
              ▼           ▼              ▼
          repos/       llm/           rules/
      SQLAlchemy    LLM Provider    純規則模組
      (PG/SQLite)   + Capability    (sql_safety,
              │      Adapter 層      diff, advisor…)
              ▼           │
          jobs 表 ◄── workers/  asyncio 背景 worker
                          │
                          ▼
                 OpenAI 相容 gateway / 使用者的 PostgreSQL
```

**鐵律**：
- `api/` 只做 HTTP 進出與驗證，不含業務邏輯
- `services/` 不 import FastAPI；`llm/` 與 `rules/` 不碰資料庫
- 所有 LLM 呼叫**只能**經過 `llm/provider.py`，禁止任何模組直接 import `openai`

### 3-3 專案目錄結構（目標）

```
sql_agent/
├── pyproject.toml               # 依賴 + ruff + pytest 設定
├── alembic/                     # migrations
├── app/
│   ├── main.py                  # FastAPI app factory + lifespan（啟動 worker、載入設定）
│   ├── config.py                # pydantic-settings：所有環境變數
│   ├── api/                     # routers：sessions / messages / outputs / agent /
│   │   │                        #   changes / workbench / settings / auth / events(SSE)
│   │   └── schemas/             # Pydantic request/response models
│   ├── services/                # session_service / interview_service / generation_service /
│   │                            #   review_service / agent_service / change_service / workbench_service
│   ├── repos/                   # SQLAlchemy models + repository 函式
│   ├── llm/
│   │   ├── provider.py          # LLMProvider（AsyncOpenAI 封裝，唯一出口）
│   │   ├── capabilities.py      # 能力探測 + CapabilityProfile
│   │   ├── adapters.py          # 降級轉接（詳見第四章）
│   │   ├── structured.py        # structured output helpers（Pydantic → json_schema）
│   │   └── prompts/             # 所有 system prompt 純文字檔
│   ├── rules/                   # 從 v0.5 幾乎原樣移植的純規則模組（見第八章）
│   ├── workers/                 # job runner + generate/review job handlers
│   └── web/                     # Jinja templates + static（見第九章前端結構）
├── tests/
└── docs/
```

---

## 四、LLM 呼叫層設計（本次重建的核心）

### 4-1 原則

1. **標準優先**：預設一律送標準 OpenAI Chat Completions 多輪格式——
   `messages` 陣列、string content、獨立 `system` role、原生 `tools`、
   `response_format: json_schema`。**多輪對話靠 messages 陣列表達，
   不再把歷史攤平塞進單一訊息**——攤平只存在於降級轉接層的最後手段。
2. **相容性收斂到一層**：v0.5 的 `LLM_SYSTEM_MODE` × `LLM_CONTENT_FORMAT`
   環境變數矩陣全部移除，改為自動偵測的 `CapabilityProfile` + `adapters.py`
   單一降級點。業務程式碼（services、agents）永遠寫「標準用法」，
   完全不知道降級的存在。
3. **一個出口**：`LLMProvider` 是唯一呼叫 LLM 的類別，統一負責 retry
   （429/5xx 指數退避）、timeout、結構化 request log（begin/done、耗時、
   token 用量）、串流。

### 4-2 `LLMProvider` 介面

```python
class LLMProvider:
    async def chat(self, messages: list[Message], *,
                   tools: list[ToolDef] | None = None,
                   response_model: type[BaseModel] | None = None,
                   stream: bool = False) -> ChatResult | AsyncIterator[ChatChunk]

@dataclass
class ChatResult:
    text: str | None            # 一般文字回覆
    tool_calls: list[ToolCall]  # 原生 function calling 結果
    parsed: BaseModel | None    # response_model 有給時的結構化結果
    usage: Usage
```

- `response_model` 給 Pydantic 類別 → 內部轉成 `response_format={"type":"json_schema", ...}`，
  回應自動驗證成該模型實例（gateway 不支援時由 adapter 降級，呼叫端無感）
- `stream=True` → 回傳 async iterator，由 SSE endpoint 直接轉發給前端

### 4-3 CapabilityProfile 與降級轉接

**能力探測**（`capabilities.py`）在「設定頁儲存 LLM 連線時」與「手動打
`POST /api/llm/diagnose`」時執行一次（不在每次請求執行），結果持久化到
`app_settings`，格式：

```json
{
  "multi_turn": true,          // 歷史探針：三則訊息問暗號
  "system_role": true,         // system prompt 探針：SYSMARK
  "native_tools": true,        // 送一個 dummy tool，看是否回 tool_calls
  "json_schema": true,         // 送 response_format，看是否回合法 JSON
  "streaming": true,           // stream=True 是否正常回 chunk
  "probed_at": "ISO8601"
}
```

**降級鏈**（`adapters.py`，每項能力一個轉接器，可獨立疊加）：

| 能力缺失 | 降級行為 | 對應 v0.5 的舊機制 |
|---|---|---|
| `native_tools=false` | 工具目錄改注入 system prompt，要求模型輸出 JSON 格式的工具呼叫（單一 JSON 區塊，非 XML 標籤），provider 解析後包裝成 `ToolCall` 回傳 | `<TOOL>` 標籤協定（簡化重寫） |
| `json_schema=false` | 在 prompt 中附上 schema 說明要求輸出 JSON，回應以 Pydantic 寬鬆解析（含 markdown code fence 剝除、一次自動重試） | `<TABLE_SPECS>` 標籤解析 |
| `system_role=false` | system 內容併入第一則 user 訊息開頭 | `LLM_SYSTEM_MODE=inline` |
| `streaming=false` | 非串流呼叫後一次性回傳（SSE 端仍照常推一個完整 event，前端無感） | —（新功能） |
| `multi_turn=false` | **最後手段**：整段歷史攤平成單一 user 訊息 | `LLM_SYSTEM_MODE=single_turn` |

**設定介面**：只留一個選配環境變數 `LLM_FORCE_PROFILE`（JSON，覆蓋自動偵測，
供除錯），其餘 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` / `LLM_VERIFY` /
`LLM_TIMEOUT` 照舊。`GET /api/llm/health` 保留（單次 ping + 回傳目前 profile）。

### 4-4 各功能的 LLM 用法對照

| 功能 | v0.5 作法 | v2 作法 |
|---|---|---|
| Interviewer 需求收集 | 每輪把回覆丟給 XML 標籤解析找 `<TABLE_SPECS>` | 多輪 messages + `response_model=InterviewTurn`（Pydantic：`reply: str`、`tables: list[TableSpec] | None`、`summary: list[str] | None`）——一次呼叫同時拿到回覆文字與結構化 schema |
| DB Agent 工具迴圈 | `<TOOL>`/`<OBSERVATION>` 文字協定，人肉重建 transcript | 原生 function calling 迴圈：`tools=registry.tool_defs()` → 收到 `tool_calls` → 執行 → 以 `role:"tool"` 訊息回填 → 續呼叫（上限 8 步不變；`propose_ddl` 仍為 terminal） |
| Writers（DDL/Diagram/Security） | 單發 `chat(system, human)` | 不變（單發呼叫），但走 provider 統一出口；DiagramWriter 的 Mermaid 仍**確定性產生**、SpecWriter 仍零 API |
| Reviewer | 單發呼叫 | 不變 |
| NL2SQL | 單發呼叫 | `response_model=SQLDraft`（`sql: str`、`explanation: str`），杜絕從自由文字撈 SQL |
| 對話串流 | 無 | Interviewer 與 DB Agent 的文字回覆改走 streaming → SSE |

---

## 五、資料層設計

以 `docs/db_schema.md` 的 v1.0 目標 schema 為基礎，調整如下：

- `users`、`sessions`、`messages`、`schema_versions`、`table_specs`、
  `column_specs`、`context_table_specs`、`activity_log`、`change_requests`
  照該文件實作，但 **`table_specs`/`column_specs` 收斂為 JSON 欄位**：
  `schema_versions.tables_json`（`list[TableSpec]` 的 JSON）——
  結構化拆表對本產品沒有查詢需求（永遠整組讀寫），拆成三層表只增加 join
  與遷移成本，也讓 SQLite 相容（無 `text[]` 型態）。`context_table_specs`
  同理收斂為 `sessions.context_tables_json`。
- 新增 `jobs` 表：`{id, session_id, kind(generate|review|extra), status(queued|running|done|failed), payload_json, progress_json, error, created_at, started_at, finished_at}`。
  生成進度（每份文件 waiting/loading/done/failed）存 `progress_json`，
  SSE endpoint 監看此表推播。
- `outputs` 存 `outputs` 表（`{session_id, filename, content, created_at}`），
  不再內嵌於 session JSON。文件量小（Markdown/SQL 文字），直接進 DB，
  不引入 object storage（規模不需要；介面上 repo 層隔離，未來可換）。
- 所有表帶 `user_id`（v2 初期可為 NULL，Phase 7 認證上線後強制）。
- DB 連線字串（session 匯入用、業務 DB 設定）以 **AES-256-GCM 加密**存放
  （金鑰 `DB_ENCRYPTION_KEY` 環境變數），API 回應與 log 一律遮罩——
  依 `docs/security_design.md` 第五章。

---

## 六、API 設計

路由與 v0.5 對齊（前端遷移成本低），全部掛 `/api/v1/` 前綴，
Pydantic schema 進出、OpenAPI 自動生成。重點差異：

| 端點 | 說明 |
|---|---|
| `POST /api/v1/sessions/{id}/messages` | 回 SSE 串流（`Accept: text/event-stream`）：`delta` events（文字增量）→ 最後一個 `turn_done` event（含 `tables_ready`、結構化 tables）。也支援非串流 JSON 模式（相容測試） |
| `GET /api/v1/sessions/{id}/events` | SSE：生成進度（取代 2 秒輪詢）。job 狀態變化即推 `generation_status` event |
| `POST /api/v1/agent/chat` | DB Agent 對話，SSE：`tool_call` / `tool_result` / `delta` / `turn_done` events（前端步驟軌跡即時顯示，不必等整回合結束） |
| `GET /api/v1/llm/health`、`POST /api/v1/llm/diagnose` | 健康檢查 + 觸發能力探測（回傳/更新 CapabilityProfile） |
| 其餘 | sessions CRUD、confirm、versions/restore、outputs(+zip)、extras、ddl-import、workbench（query/explain/nl2sql/schema-tree/validate-ddl）、change-requests（approve/reject）、settings、activity 全數保留，行為同 v0.5 |

---

## 七、安全設計

依 `docs/security_design.md` 全文為準，v2 落地順序：

- **第一天就做**：SQL 唯讀護欄與 DDL allowlist（移植 `sql_safety.py`）、
  連線字串加密 + 遮罩、Pydantic 輸入驗證（訊息 ≤10,000 字元等）、
  結構化 audit log、`SECRET_KEY` 預設值啟動檢查
- **Phase 7 做**：JWT 認證（HS256、access 15 分/refresh 7 天、HttpOnly cookie）、
  session 所有權驗證、rate limiting、`ADMIN_TOKEN` 過渡機制升級為 admin 角色
- 認證上線前，`ADMIN_TOKEN` header 機制照 v0.5 保留給 change request 審批

---

## 八、可直接移植的模組（不重寫）

以下純規則模組與 LLM/框架無關、已有測試，v2 **原樣搬進 `app/rules/`**
（只改 import 路徑），節省大量工時：

`sql_safety.py`（skeleton/split/check_read_only/check_ddl_allowlist）、
`schema_diff.py`、`schema_advisor.py`、`schema_remediation.py`、
`convention_checker.py`、`table_relation.py`、`metadata_checker.py`、
`ddl_parser.py`、`db_introspect.py`（改 async 或跑在 thread executor）、
`ddl_validator.py`、`ddl_executor.py`、純模板 writers（spec/dbml/plantuml/
json_schema/data_dict）、`models/schema.py` 的 `TableSpec`/`ColumnSpec`
（改寫成 Pydantic 模型，同時作為 structured output 的 json_schema 來源）。

對應的既有測試一併搬移，作為移植正確性的驗證。

---

## 九、前端設計（含 Google Stitch 置換預備）

### 9-1 結構分層（Stitch-ready 的關鍵）

前端刻意分成三層，**結構與皮膚完全分離**：

```
app/web/
├── templates/            # Jinja2：只有語意化結構（semantic markup + 元件 class）
│   ├── base.html
│   └── index / chat / confirm / docs / review / agent / settings .html
├── static/js/            # ES modules：邏輯只掛 data-* attribute，不依賴視覺 class
└── static/css/
    ├── tokens.css        # ★ 唯一的「皮膚」來源：CSS custom properties
    ├── components.css    # 元件樣式：只允許引用 tokens.css 的變數
    └── pages.css         # 版面配置（grid/flex 結構）
```

**規範（實作時強制）**：
1. `tokens.css` 定義全部設計變數：色板（primary/surface/text/semantic 各階）、
   字型家族與級距、間距尺度、圓角、陰影、邊框。
2. `components.css` 與 `pages.css` **禁止出現任何字面色碼/px 字級**，
   一律 `var(--token-name)`。
3. JS 綁定一律用 `data-action` / `data-target` attribute，
   換 class 名稱不會弄壞行為。
4. 建立**元件清單文件**（`docs/ui_components.md`）：button（primary/ghost/danger）、
   card、chat bubble（user/ai/tool-step）、data table、tabs、progress、
   badge/status pill、modal、toast、form field、code block、sidebar、
   diff 標記——每個元件一個 class 名 + 狀態變體，附截圖。

### 9-2 【預留計畫】Google Stitch 設計風格整合（Phase 8）

> **狀態：預留。** 未來會由使用者提供 Google Stitch 產出的設計
> （Stitch 輸出為 HTML + Tailwind 風格 utility class 的靜態頁面設計稿，
> 或 Figma 匯出）。本階段在設計稿到位前**不動工**，但前述 9-1 的分層
> 就是為此準備的整合介面。

設計稿到位後的整合工序（給實作 AI 的固定流程）：

1. **萃取 tokens**：從 Stitch 輸出的 HTML/Tailwind class 或 Figma 變數中，
   萃取色板、字型、間距、圓角、陰影 → 覆寫 `tokens.css`（唯一必改檔）。
2. **元件比對**：以 `docs/ui_components.md` 的元件清單逐一對照 Stitch 設計稿，
   調整 `components.css`；元件的 DOM 結構若需變動，僅允許改 templates 中
   對應元件的 markup，**不得動 `data-*` attribute 與 JS**。
3. **版面調整**：依設計稿改 `pages.css` 的 grid/flex 配置。
4. **不改動範圍**：API、services、JS 邏輯、路由——設計置換是純視覺層工程。
5. **驗證標準**：所有頁面功能測試（Playwright smoke）通過；
   深/淺色（若設計稿有提供）皆正常；1280px+ 桌面版面無橫向捲軸。
6. Stitch 若提供多頁設計稿但風格不一致，以其提供的「首頁 + 對話頁」為
   基準風格，其餘頁面按元件庫推導，並回報差異清單給使用者確認。

在此之前（Phase 6）先以 v0.5 現有視覺風格做一版乾淨的 token 化預設皮膚
（深藍/灰白、藍綠強調色，照 `docs/platform_design_spec.md` 第七章）。

---

## 十、分階段實作計畫（交付 Sonnet 5 執行）

> 每階段結尾必須：測試全綠、`ruff check` 乾淨、更新 README 與本文件的
> 進度勾選、單獨 commit。任一驗證不過不得進下一階段。
> LLM 相關測試一律用 respx mock HTTP，不需真實 gateway。

### Phase 0 — 專案骨架（0.5 天）
- 建立 `pyproject.toml`（fastapi、uvicorn、openai、sqlalchemy[asyncio]、
  alembic、pydantic-settings、cryptography、pytest、pytest-asyncio、respx、ruff）
- `app/` 目錄骨架、`config.py`（pydantic-settings 讀 `.env`）、
  `main.py`（health endpoint）、GitHub Actions CI（ruff + pytest）
- **驗證**：`uvicorn app.main:app` 啟動、`GET /healthz` 200、CI 綠

### Phase 1 — LLM Provider 層（2 天）★ 本次重建核心
- `llm/provider.py`：AsyncOpenAI 封裝（chat / stream / retry / 結構化 log）
- `llm/structured.py`：`response_model` → json_schema 與寬鬆解析 fallback
- `llm/capabilities.py`：五項能力探針 + profile 持久化
- `llm/adapters.py`：五個降級轉接器（表格見 4-3）
- **驗證**：單元測試涵蓋——標準路徑、429 退避、每個能力缺失時的降級行為、
  串流分塊、structured output 解析失敗自動重試一次；
  `pytest tests/llm/ -v` 全綠

### Phase 2 — 資料層（1.5 天）
- SQLAlchemy 2.0 async models（第五章 schema）+ Alembic 初始 migration
- repos：sessions / messages / versions / outputs / jobs / settings /
  change_requests / activity；連線字串加密工具
- **驗證**：repo 層測試（SQLite in-memory）全綠；
  `alembic upgrade head` 在 PostgreSQL 與 SQLite 皆成功

### Phase 3 — Sessions API + Interviewer（2 天）
- sessions CRUD、messages（SSE 串流 + JSON 模式）、confirm、
  versions/restore、`llm/health` + `diagnose`
- `interview_service`：多輪 messages 組裝、`InterviewTurn` structured output、
  現有 DB context 注入規則（sticky，同 v0.5）
- **驗證**：mock LLM 的端到端測試——多輪對話 → `tables_ready` →
  confirm 建立 job；SSE 串流 event 順序正確；OpenAPI `/docs` 完整列出端點

### Phase 4 — 生成 Worker + Writers + 審查模式（2 天）
- `workers/`：job runner（asyncio task，輪詢 `jobs` 表）+ generate/review/extra handlers
- writers 移植：spec（零 API）、diagram（Mermaid 確定性 + LLM 說明文字）、
  ddl、security；on-demand extras（orm/migration/query/incremental + 4 個純模板）
- review 模式：db_introspect + Reviewer + schema_advisor 紅旗 + remediation SQL
- outputs API + zip；`GET .../events` SSE 進度
- **驗證**：mock LLM 下四文件並行產出、單檔失敗不影響其他、
  SSE 進度 event 正確；審查模式對 fixture schema 產出報告 + 紅旗

### Phase 5 — DB Agent（native tool calling）+ HITL（2 天）
- `agent_service`：原生 function calling 迴圈（8 步上限、transcript 從 DB 重建、
  observation 截斷、token 預算裁剪）
- tool registry 移植（11 個工具，handler 改接 v2 rules/repos）
- change requests：propose_ddl terminal 工具、approve（重驗 + 單一交易執行）
  /reject、`ADMIN_TOKEN` 過渡保護
- **驗證**：mock LLM 回傳 `tool_calls` 的多步推理測試；
  native_tools=false 降級路徑同樣通過；DDL allowlist/dry-run 測試移植全綠

### Phase 6 — 前端（3 天）
- 9-1 的三層結構：tokens.css（v0.5 風格）、components.css、templates、ES modules
- 六頁面 + DB Agent 抽屜 + SQL 工作台；SSE 客戶端（EventSource，斷線 3 次重試
  後顯示「請重新整理」，NFR-02）
- `docs/ui_components.md` 元件清單
- **驗證**：Playwright smoke（建 session → 對話 → 確認 → 看文件下載 zip；
  審查模式；agent 提問）；無輪詢請求（Network 面板確認）

### Phase 7 — 認證與安全強化（2 天）
- users 表 + JWT（登入/refresh/登出撤銷）、session 所有權驗證、
  admin 角色（取代 ADMIN_TOKEN）、rate limiting、audit log 事件補齊
- **驗證**：`docs/security_design.md` 第八章缺口清單逐項勾銷；
  未授權存取他人 session 回 403 測試

### Phase 8 — 【預留】Google Stitch 設計整合
- 見 9-2；設計稿到位後啟動，工序與驗證標準已定義

### Phase 9 — 部署（1 天）
- Dockerfile（multi-stage）+ docker-compose（app + PostgreSQL）、
  gunicorn/uvicorn worker 設定、`.env.example` 更新、
  `docs/deployment_guide.md` 改寫
- **驗證**：`docker compose up` 後完整流程可跑；README 安裝步驟照做可用

---

## 十一、分支與切換策略

- v2 在本 repo 的**孤兒分支 `v2`** 上全新開發（無 main 歷史、零舊程式碼，
  起點只有需求 MD 文件與本計畫書）；v0.5 完整程式碼保留於 `main` 分支，
  供隨時對照與移植取檔（第八章的純規則模組實作時從 `main` 分支複製）。
- v0.5 舊 session 資料**不遷移**（已與使用者確認）：v2 上線後從空資料庫開始，
  舊資料留在原處可查。
- 描述 v0.5 舊實作的文件（architecture / system_architecture /
  deployment_guide / ops_runbook）已移至 `docs/v05/` 作歷史參考；
  `docs/` 根目錄保留的均為需求與目標設計文件（v2 的需求來源）。
- 環境變數變更：移除 `LLM_SYSTEM_MODE`、`LLM_CONTENT_FORMAT`；
  新增 `DB_ENCRYPTION_KEY`、`LLM_FORCE_PROFILE`（選配）。`.env.example` 同步。

## 十二、風險與開放問題

| 風險 | 緩解 |
|---|---|
| 內網 gateway 可能連 native tools/json_schema 都不支援 | 降級鏈完整覆蓋到 v0.5 等價行為（含 single_turn 攤平），最壞情況功能不退步；能力探測結果在設定頁可視 |
| openai SDK 對自簽憑證 gateway 的 SSL 設定 | SDK 支援自訂 `http_client`（httpx，`verify=False`）；Phase 1 測試涵蓋 |
| SQLite/PostgreSQL 行為差異 | repo 層測試雙後端跑（CI matrix）；避免 PG 專屬型態 |
| Stitch 設計稿格式未知 | 9-2 已定義以 tokens 為唯一整合點；任何格式（HTML/Tailwind/Figma）都先人工萃取成 tokens |
