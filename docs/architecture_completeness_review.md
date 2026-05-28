# 架構完整度檢核報告（依 DEVELOPMENT_GUIDELINES.md）

檢核日期：2026-05-28
協作分支：`gpt-collab/architecture-completeness-20260528`

## 專案理解

SQL Agent 是一個資料庫建檔管理 Agent 系統，提供 Flask 網頁平台與 CLI 兩種入口。核心流程是透過對話式 AI 收集資料表設計需求，確認後輸出規格書、ER Diagram、PostgreSQL DDL、效能安全規劃；另外提供現有 PostgreSQL DB 匯入與審查模式。

目前架構屬於 `v0.5 Core features in development`：核心功能已具備，但距離可長期營運的平台仍缺少權限、部署、監控、API 契約與完整測試。

## 檢核摘要

| Guideline 面向 | 目前狀態 | 完整度 | 缺口 |
|---|---|---:|---|
| 1. Project Initiation | README 與平台設計文件已有背景、目標、使用者 | 70% | 缺正式 project charter、明確 out-of-scope、成功指標 |
| 2. Requirements Analysis | `docs/platform_design_spec.md` 有使用者與流程 | 55% | 缺 user stories、permission matrix、data source inventory、workflow diagrams |
| 3. System Architecture | `docs/architecture.md` 已描述 Flask、CLI、session、worker、資料流 | 75% | 缺部署架構圖、安全設計文件、正式 API specification |
| 4. UI / UX Design | 有頁面流程與模板 | 60% | 缺完整 wireframes、互動 prototype、錯誤狀態規格 |
| 5. Database Design | 目前使用 JSON session，不是正式 DB | 35% | 缺 ERD、資料字典、migration、audit table 設計 |
| 6. Backend Development | Flask API、背景 worker、DB introspection 已有 | 65% | 缺 auth/session、RBAC、標準 API spec、完整 state-changing audit |
| 7. Frontend Development | templates/static 已具備主要頁面 | 60% | 缺 permission-aware UI、完整 loading/error 規格、可存取性檢查 |
| 8. Data Integration and Scheduling | 已有 PostgreSQL introspection；無排程 | 45% | 缺 import batch record、錯誤報告格式、idempotency/lock 設計 |
| 9. Testing and Acceptance | 有 pytest，但覆蓋很少 | 25% | 缺 API、integration、permission、data accuracy、UAT 測試 |
| 10. Deployment and Go-live | README 有本機啟動 | 30% | 缺正式 deployment guide、env checklist、backup/restore、smoke test |
| 11. Documentation and Training | README、architecture、platform design 已有 | 55% | 缺 user manual、API reference、operations runbook、incident SOP |
| 12. Operations and Monitoring | 本次新增 system log 基礎能力 | 45% | 缺 health check、alert、log rotation、backup monitor |
| 13. Version Control | GitHub 管理；本次建立 GPT 協作 branch | 60% | 缺 release notes、版本策略、rollback SOP |
| 14. Core Principles | 可理解、可擴充雛形已有 | 55% | traceable/verifiable/controlled/observable/recoverable 尚未完整 |

## 本次已補強

### 1. 系統事件日誌

新增 `web/system_log.py`，以 JSONL 形式寫入 `logs/system.log.jsonl`。目前記錄事件包含：

- `session_created`
- `db_imported`
- `db_import_failed`
- `tables_ready`
- `generation_started`
- `generation_file_done`
- `generation_file_failed`
- `generation_finished`
- `review_finished`
- `review_failed`
- `outputs_downloaded`
- `version_restored`
- `api_error`

敏感欄位包含 `token`、`password`、`secret`、`db_url`、`authorization`、`cookie`，寫入前會遮蔽為 `***REDACTED***`。

### 2. API 錯誤格式標準化

新增 `_api_error(message, status_code)`，API 端點統一回傳：

```json
{"error": "message"}
```

並補上 API 404 與 500 handler，避免 API 使用者收到 HTML 錯誤頁。

### 3. 模式輸入驗證

`POST /api/sessions` 現在只允許：

- `design`
- `review`

其他值會回傳 HTTP 400。

### 4. 基礎測試

新增：

- `tests/test_system_log.py`
  - 驗證 JSONL log 寫入
  - 驗證敏感欄位遮蔽
- `tests/test_app_errors.py`
  - 驗證 API 404 JSON 格式
  - 驗證 invalid mode 被拒絕

## 建議下一步實作順序

### P0：避免營運風險

1. 新增 `/api/health` 健康檢查端點。
2. 新增 `docs/deployment_guide.md`，包含 Windows / IIS / FastCGI 或一般 Flask 啟動方式。
3. 新增 `docs/operations_runbook.md`，定義 log 位置、常見錯誤、重啟與備份方式。
4. 補 API route tests：session create/list/get、message empty content、confirm no tables、outputs no outputs。

### P1：讓架構可維護

1. 補 `docs/api_reference.md`，列出 request/response/status code。
2. 補 `docs/data_dictionary.md`，至少先描述 `data/{session_id}.json` schema。
3. 把 `app.py` 過大的 route 集中問題拆成 blueprint：page routes、session API、output API。
4. 補 `session_store` tests：create/update/set_tables/restore/try_start_generation。

### P2：接近正式平台

1. 設計登入與權限層，至少先有 admin/user/viewer 角色矩陣。
2. 將 JSON session store 抽象成 repository interface，為未來 PostgreSQL persistence 做準備。
3. 增加正式 audit log：state-changing operation 需要 actor、timestamp、before/after 或 event context。
4. 增加 backup/restore SOP 與自動備份策略。

## 暫不建議現在做的事

- 不建議立刻把 JSON session 全面改 PostgreSQL，會牽動大、風險高。
- 不建議先做完整 RBAC UI，因為目前尚未有登入機制與使用者資料模型。
- 不建議為單一用途先抽象過多 service layer；可以等 API 測試補齊後再拆 route。

## 驗收標準

本次協作分支應符合：

1. 不修改 `main`。
2. 新增的 runtime log 不被 commit。
3. API 錯誤維持 `{ "error": "..." }` 格式。
4. 敏感資訊不可寫入 system log。
5. `pytest tests/ -v` 應通過。
