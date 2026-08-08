# SQL Agent v2

透過對話式 AI 收集資料表設計需求，自動產出規格書、ER Diagram、DDL、效能安全規劃四份技術文件，並提供現有資料庫審查、DB Agent 助手與人工審批（HITL）變更流程。

> **目前狀態：Phase 0–9 全部完成。**
> 已完成：Phase 0 專案骨架、Phase 1 LLM Provider 層（openai SDK + 能力探針 +
> 降級轉接）、Phase 2 資料層（SQLAlchemy 2.0 async + Alembic + 加密）、
> v0.5 純規則模組移植（`app/rules/`）、Phase 3–5（sessions / 生成 worker /
> 審查 / DB Agent + HITL 的 services 與 API 端點）、Phase 6 前端接線
> （`app/web/`：七頁 + 全站 DB Agent 抽屜，SSE 串流、無輪詢）、
> Phase 7 JWT 認證（`AUTH_ENABLED` 預設關閉，啟用方式見 docs/deployment.md
> 與 `.env.example`）、Phase 8 Google Stitch「Pro Space Gray」深色設計置換
> （權威規格：`docs/design/stitch_design_pro_space_gray.md`）、
> Phase 9 部署（Dockerfile / docker-compose）。
> v0.5 完整實作保留在 `main` 分支。
>
> **上線前可用性強化（依使用者訪談與功能盤點）：** 業務資料庫連線的增刪改由
> `ADMIN_TOKEN`/admin 角色保護（見 `docs/permission_matrix.md`）、核准 DDL 前
> 加上帶影響評估的確認對話框、文件頁真正渲染 Markdown 與 SQL 並支援瀏覽器列印
> 另存 PDF、外部 CDN 資產全部落地（內網可離線運作）、差異比對改由後端計算
> （會標出型態與長度變更）、session 可命名/刪除/搜尋。
> 另有：對話歷史可還原、確認頁可直接以建表語法編輯結構、產出失敗可重跑、
> 設計模式可匯入現有資料庫、DB Agent 可開新對話、審查報告改走結構化輸出
> （Markdown 由平台排版，不再靠正則解析 LLM 自由書寫的文字）。
>
> 本階段新增端點：`DELETE /sessions/{id}`、`GET /sessions/{id}/messages`、
> `GET|PUT /sessions/{id}/tables-ddl`、`POST /agent/conversations`。
> `agent` / `settings` / `activity` / `change-requests` 補上認證依賴——
> `AUTH_ENABLED=false` 的行為完全不變，`true` 時才會實際生效。
>
> **使用者功能強化（依三位使用者角色的討論與逐階段驗收，見
> [`docs/user_feature_backlog.md`](docs/user_feature_backlog.md)）：**
> DB Agent 頁新增「查詢資料」分頁——一個唯讀查詢工作台，含四種模式：
> 中文提問（nl2sql 產生語法後直接執行，語法收在進階區）、自己寫 SQL、
> 查詢為什麼慢（EXPLAIN 樹狀圖，掃全表標紅並附白話說明）、
> 找資料表（結構瀏覽器 + 資料字典，可為表與欄位加註說明與負責人）。
> 結果附「尚未經人工覆核」標記與帶 BOM 的 CSV 匯出（Excel 可直接開啟中文）。
> 確認頁 DDL 編輯器加「驗證語法」；文件頁 ER 圖可下載 SVG、術語有白話解釋；
> 首頁每筆紀錄顯示「下一步要做什麼」的白話進度。
>
> 同期修正三個既有缺陷：唯讀護欄改用允許清單（原本 `EXPLAIN ANALYZE DELETE`、
> `setval()`、`dblink_exec()`、`CALL`、`COPY ... TO PROGRAM` 皆可通過，
> 且此路徑不經 HITL）；ER 關聯圖原本在隱藏分頁渲染導致每份文件的圖都畫不出來；
> 中文表名原本被逐字轉成底線。
>
> 本階段新增端點：`POST /workbench/{query,explain,nl2sql}`、
> `GET /workbench/schema-tree`、`GET|PUT /workbench/dictionary`、
> `POST /sessions/{id}/validate-ddl-text`。前四個以業務資料庫名稱為範圍
> （DB Agent 頁沒有 session），與既有 session 範圍的版本共用 service 層核心。
>
> 開發環境：`pip install -e ".[dev]"`；測試 `python3 -m pytest`；lint `ruff check .`
>
> **測試分層**（見 `CLAUDE.md` 第 4.2 節）：`tests/rules|repos`（純邏輯）、
> `tests/web/test_contract.py`（前端依賴的 API 形狀）、`tests/architecture`
> （跨檔案約定，例如分層方向、錯誤訊息必須是中文）、`tests/gateway`
> （LLM gateway 能力降級）、`tests/e2e`（瀏覽器煙霧測試，預設不跑，
> `pip install -e ".[e2e]" && python -m playwright install chromium`
> 之後以 `pytest -m e2e` 執行）。

---

## 從這裡開始

**實作前必讀（依序）：**

1. [`docs/v2_rebuild_plan.md`](docs/v2_rebuild_plan.md) — **v2 重建架構計畫書**：技術選型、LLM 呼叫層設計、分層架構、Phase 0–9 分階段實作計畫（含每階段驗證標準）。實作者照此文件逐階段執行。
2. [`CLAUDE.md`](CLAUDE.md) — 行為準則（簡單優先、不做規格外功能、每階段可驗證）
3. [`DEVELOPMENT_GUIDELINES.md`](DEVELOPMENT_GUIDELINES.md) — 全生命週期開發準則

## 需求文件索引（`docs/`）

| 文件 | 內容 |
|---|---|
| [`project_charter.md`](docs/project_charter.md) | 專案背景、目標、範圍、成功標準 |
| [`requirements_spec.md`](docs/requirements_spec.md) | 功能性需求（FR-01～07）與非功能性需求（NFR-01～05） |
| [`user_stories.md`](docs/user_stories.md) | 使用者故事與驗收標準 |
| [`platform_design_spec.md`](docs/platform_design_spec.md) | 平台頁面結構、使用流程、UI 狀態 |
| [`db_schema.md`](docs/db_schema.md) | 平台自身資料庫的目標 schema |
| [`security_design.md`](docs/security_design.md) | 威脅模型、JWT 認證、敏感資料處理 |
| [`permission_matrix.md`](docs/permission_matrix.md) | 角色權限矩陣 |
| [`workflow_diagrams.md`](docs/workflow_diagrams.md) | 工作流程圖 |
| [`test_cases.md`](docs/test_cases.md) | 測試案例 |
| [`go_live_checklist.md`](docs/go_live_checklist.md) | 上線檢查清單 |
| [`feature_backlog.md`](docs/feature_backlog.md) | 系統面缺口盤點與優先順序（安全、稽核、agent 行為；含已複驗的證據與裁決） |
| [`user_feature_backlog.md`](docs/user_feature_backlog.md) | 使用者功能盤點（三方使用者角色辯論後整合，依後端現況分批） |
| [`work_log_2026-08.md`](docs/work_log_2026-08.md) | 2026-08 工作紀錄：討論方法、實作與驗收過程、實測發現的缺陷、被推翻的判斷 |
| `v05/` | v0.5 舊實作的架構/部署/維運文件（歷史參考，不適用於 v2） |

## 開發輔助 Skills（`.claude/skills/`）

專案層級的 Claude Code skills，開啟本 repo 的 session 會自動載入。完整使用說明見
[`.claude/skills/README.md`](.claude/skills/README.md)。

| Skill | 觸發方式 | 做什麼 |
|---|---|---|
| `cost-aware-orchestration` | 自動 | 決定任務要主模型直接做、派子代理、還是進 plan mode |
| `styleseed` | 自動 | 前端 UI／設計系統的資深設計判斷 |
| `ponytail` | `/ponytail [lite\|full\|ultra]` | 懶惰資深工程師模式，強制最短可動解法 |
| `i-have-adhd` | `/i-have-adhd` | 把回應排版成 ADHD 讀者可直接行動的形式 |
| `ponytail-review` / `-audit` / `-debt` / `-gain` / `-help` | 同名指令 | 過度設計檢查、技術債帳本、速查卡（只讀不寫） |

## 分支說明

| 分支 | 內容 |
|---|---|
| `v2`（本分支） | v2 全新開發線：從零開始，依 `docs/v2_rebuild_plan.md` 分階段實作 |
| `main` | v0.5 完整實作：Flask + 手刻 LLM 客戶端版本，計畫書第八章所列純規則模組（sql_safety、schema_diff、convention_checker 等）實作時從此分支移植 |
