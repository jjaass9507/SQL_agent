# SQL Agent v2

透過對話式 AI 收集資料表設計需求，自動產出規格書、ER Diagram、DDL、效能安全規劃四份技術文件，並提供現有資料庫審查、DB Agent 助手與人工審批（HITL）變更流程。

> **目前狀態：規劃階段（尚未開始實作）。**
> 本分支（`v2`）是專案全面重建的起點：不含任何程式碼，只有需求文件與重建計畫書。
> v0.5 的完整實作保留在 `main` 分支，供對照與模組移植。

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
| `v05/` | v0.5 舊實作的架構/部署/維運文件（歷史參考，不適用於 v2） |

## 分支說明

| 分支 | 內容 |
|---|---|
| `v2`（本分支） | v2 全新開發線：從零開始，依 `docs/v2_rebuild_plan.md` 分階段實作 |
| `main` | v0.5 完整實作：Flask + 手刻 LLM 客戶端版本，計畫書第八章所列純規則模組（sql_safety、schema_diff、convention_checker 等）實作時從此分支移植 |
