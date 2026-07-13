# UI 元件清單

> 對應 `docs/v2_rebuild_plan.md` 9-1／9-2：本文件是未來 Google Stitch 設計置換
> 時的比對基準。每個元件列出 class 名、狀態變體、用途、所屬 token 依賴。
> 樣式定義於 `app/web/static/css/components.css`，版面配置於 `pages.css`，
> 全部變數來源於 `tokens.css`（唯一皮膚來源，見該檔規範）。

## Button — `.btn`

| class | 用途 |
|---|---|
| `.btn` | 基礎按鈕 |
| `.btn-primary` | 主要操作（送出、確認、儲存） |
| `.btn-ghost` | 次要操作（取消、返回） |
| `.btn-danger` | 危險操作（拒絕、刪除） |
| `.btn-accent` | 強調操作（前往下一步） |
| `.btn-sm` / `.btn-lg` | 尺寸變體 |
| `:disabled` | 停用狀態 |

Token 依賴：`--color-primary-600/700`、`--color-error-500/600`、`--color-accent-500/600`、
`--color-surface-0`、`--color-text-700`、`--radius-md`、`--space-*`、`--font-size-base/sm/md`。

## Card — `.card`

用途：內容容器（session 卡片、設定分區、報告分段）。
變體：`.card-title`（標題）、`.card-title-spaced`（次段標題間距）、`.card-clickable`（可點擊，hover 陰影）。
Token 依賴：`--color-surface-0`、`--border-color`、`--radius-lg`、`--shadow-sm/md`、`--space-5`。

## Chat bubble — `.chat-bubble`

| class | 用途 |
|---|---|
| `.chat-bubble-row` / `.chat-bubble-row-user` | 訊息列排版（AI 靠左、使用者靠右） |
| `.chat-bubble-ai` | AI 回覆氣泡（灰底） |
| `.chat-bubble-user` | 使用者訊息氣泡（深藍底） |
| `.chat-bubble-tool-step` | DB Agent 工具呼叫步驟氣泡（虛線框、等寬字） |
| `.chat-bubble-loading` | AI 思考中狀態 |

Token 依賴：`--color-surface-100`、`--color-primary-600`、`--color-accent-100/500`、
`--font-family-mono`、`--radius-lg/sm`。

## Data table — `.data-table`

用途：Schema 欄位表格、workbench 查詢結果。
變體：`.data-table-wrap`（外框）、`.data-table-header-bar`（資料表名稱列）、
`.data-table-field-name.is-pk`（主鍵欄位標示）、`.data-table-field-type`（型態，等寬字）。
Token 依賴：`--color-primary-50/800`、`--border-color-strong`、`--font-family-mono`。

## Tabs — `.tabs` / `.tab` / `.tab-panel`

用途：文件查閱頁四頁籤切換（規格書／關聯圖／DDL／安全規劃）。
狀態：`.tab.is-active`、`.tab-panel.is-active`。
Token 依賴：`--color-primary-600`、`--border-color`、`--font-weight-semibold`。

## Progress — `.progress-bar-track` / `.progress-step`

| class | 用途 |
|---|---|
| `.progress-bar-track` / `.progress-bar-fill` | 條狀進度（生成百分比） |
| `.progress-step-list` / `.progress-step` | 步驟清單（四文件產出狀態、DB Agent 工具軌跡） |
| `.progress-step.is-done/is-active/is-waiting/is-failed` | 狀態變體 |

Token 依賴：`--color-accent-500`、`--color-success-500`、`--color-error-500`、`--color-text-400`。

## Status pill — `.status-pill`

用途：session 狀態標示（進行中／待確認／已完成／失敗）。
變體：`.status-pill-in_progress`、`.status-pill-confirming`、`.status-pill-done`、`.status-pill-failed`。
Token 依賴：`--color-primary-50/600`、`--color-warning-100/600`、`--color-success-100/600`、
`--color-error-100/600`、`--radius-full`。

## Modal — `.modal-overlay` / `.modal`

用途：確認對話框、DDL 執行確認。
`.modal-overlay[hidden]` 控制顯示；`.modal-title`、`.modal-actions` 為子元件。
Token 依賴：`--z-modal`、`--shadow-lg`、`--radius-lg`。

## Toast — `.toast-stack` / `.toast`

用途：全域提示（SSE 斷線重試失敗、儲存成功等），掛在 `base.html` 的 `#toast-stack`。
變體：`.toast-error`、`.toast-success`、`.toast-warning`。
Token 依賴：`--color-primary-800`、`--z-toast`、`--shadow-md`。

## Form field — `.form-field` / `.form-input` / `.form-textarea` / `.form-select`

用途：所有表單輸入（LLM 設定、對話輸入框）。
`.form-label`（標籤）、`.form-hint`（輔助說明／空狀態文字）。
Token 依賴：`--color-text-500/900`、`--color-accent-500`（focus 邊框）、`--border-color`。

## Code block — `.code-block` / `.code-inline`

用途：DDL 顯示（深底等寬字）、行內程式碼片段。
`.code-copy-btn` 標示複製按鈕容器。
Token 依賴：`--color-primary-900`、`--font-family-mono`。

## Sidebar — `.sidebar`

用途：全站主導覽（首頁／DB Agent／設定）。
子元件：`.sidebar-logo`（含 `.sidebar-logo-accent`）、`.sidebar-nav`、
`.sidebar-nav-link`（`.is-active` 狀態）、`.sidebar-footer`。
Token 依賴：`--color-primary-900/800`、`--layout-sidebar-width`、`--color-accent-500`（active 邊條）。

## Diff 標記 — `.diff-tag`

用途：需求確認頁「與現有 DB 差異」比對（新增／刪除／變更／不變）。
變體：`.diff-tag-new`、`.diff-tag-dropped`、`.diff-tag-modified`、`.diff-tag-same`。
`.diff-item` 為單筆差異列容器。
Token 依賴：`--color-success-100/600`、`--color-error-100/600`、`--color-warning-100/600`、
`--color-surface-100`。

---

## 版面（`pages.css`，非元件，記錄供對照）

`.app-shell`、`.main-content`、`.topbar`、`.page-content` 為 base.html 共用外殼；
`.page-chat-layout`、`.page-confirm-layout`、`.page-agent-layout` 為各頁的 grid 兩欄配置；
`.page-index-*`、`.page-docs-toolbar`、`.review-*`、`.settings-*` 為各頁專屬區塊排版。

## data-* 綁定慣例

互動元素一律使用 `data-action`（觸發何種行為）與 `data-target`（指向哪個資料/DOM 節點），
JS（`app/web/static/js/pages/*.js`）只依賴這兩個 attribute，不依賴任何視覺 class 名稱——
換皮膚（Stitch 置換）時只要保留 `data-*`，class 名稱可任意調整。
