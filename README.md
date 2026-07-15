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
> 開發環境：`pip install -e ".[dev]"`；測試 `python3 -m pytest`；lint `ruff check .`

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
| [`troubleshooting.md`](docs/troubleshooting.md) | 本機啟動與內網 gateway 實測排錯（症狀→根因→解法） |
| `v05/` | v0.5 舊實作的架構/部署/維運文件（歷史參考，不適用於 v2） |

## 分支說明

| 分支 | 內容 |
|---|---|
| `v2`（本分支） | v2 全新開發線：從零開始，依 `docs/v2_rebuild_plan.md` 分階段實作 |
| `main` | v0.5 完整實作：Flask + 手刻 LLM 客戶端版本，計畫書第八章所列純規則模組（sql_safety、schema_diff、convention_checker 等）實作時從此分支移植 |
