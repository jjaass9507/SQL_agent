# UI 元件清單

> 對應 `docs/v2_rebuild_plan.md` 9-1／9-2：本文件是 Google Stitch 設計置換
> 的比對基準。每個元件列出 class 名、狀態變體、用途、所屬 token 依賴。
> 樣式定義於 `app/web/static/css/components.css`，版面配置於 `pages.css`，
> 全部變數來源於 `tokens.css`（唯一皮膚來源，見該檔規範）。
>
> Phase 8 已套用 Stitch「Pro Space Gray」皮膚（權威規格：
> `docs/design/stitch_design_pro_space_gray.md`）：深色 tiered 背景 +
> 1px hairline 邊框、無陰影／光暈（`--shadow-*` 一律 `none`）、
> System Blue 主色、Inter 13px 基準 + JetBrains Mono。

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

Token 依賴：`--color-primary-600/700`、`--color-error-500`、`--color-accent-500/600`、
`--color-bg-panel/active`、`--color-text-inverse/700/900`、`--radius-sm`、`--space-*`、
`--font-size-base/sm/md`。

## Card — `.card`

用途：內容容器（session 卡片、設定分區、報告分段）。
變體：`.card-title`（標題）、`.card-title-spaced`（次段標題間距）、
`.card-clickable`（可點擊，hover 換底色與邊框，無陰影）。
Token 依賴：`--color-bg-panel/active`、`--border-color`、`--color-primary-600`、
`--radius-lg`、`--space-4`。

## Chat bubble — `.chat-bubble`

| class | 用途 |
|---|---|
| `.chat-bubble-row` / `.chat-bubble-row-user` | 訊息列排版（AI 靠左、使用者靠右） |
| `.chat-bubble-ai` | AI 回覆氣泡（灰底） |
| `.chat-bubble-user` | 使用者訊息氣泡（深藍底） |
| `.chat-bubble-tool-step` | DB Agent 工具呼叫步驟氣泡（虛線框、等寬字） |
| `.chat-bubble-loading` | AI 思考中狀態 |

Token 依賴：`--color-bg-active`、`--color-primary-600`、`--color-accent-100/500`、
`--color-primary-300`、`--border-color`、`--font-family-mono`、`--radius-md/sm/xs`。

## Data table — `.data-table`

用途：Schema 欄位表格、workbench 查詢結果。
變體：`.data-table-wrap`（外框）、`.data-table-header-bar`（資料表名稱列）、
`.data-table-field-name.is-pk`（主鍵欄位標示）、`.data-table-field-type`（型態，等寬字）、
`.data-table-numeric`（數值欄工具 class：mono 等寬 + 右對齊）。
禁 zebra 條紋；row hover 以 `--color-bg-active` 呈現。
Token 依賴：`--color-bg-panel/active`、`--color-primary-300`、`--color-accent-500`、
`--border-color`、`--border-color-strong`、`--font-family-mono`、`--radius-md`。

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

Token 依賴：`--color-primary-600`、`--color-success-500`、`--color-error-500`、
`--color-text-400`、`--color-bg-active`。

## Status pill — `.status-pill`

用途：session 狀態標示（進行中／待確認／已完成／失敗）。
視覺：Stitch「Status Indicators」規格——8px 純色圓點（`::before`）+ 文字，
1px hairline 邊框、4px 圓角，不再用滿版底色。
變體：`.status-pill-in_progress`、`.status-pill-confirming`、`.status-pill-done`、`.status-pill-failed`。
Token 依賴：`--color-primary-600`、`--color-warning-500`、`--color-success-500`、
`--color-error-500`、`--color-bg-panel`、`--border-color`、`--radius-xs/full`、`--space-1/2`。

## Modal — `.modal-overlay` / `.modal`

用途：確認對話框、DDL 執行確認。
`.modal-overlay[hidden]` 控制顯示；`.modal-title`、`.modal-actions` 為子元件。
Token 依賴：`--z-modal`、`--color-modal-backdrop`、`--color-bg-panel`、
`--border-color`、`--radius-lg`。

## Toast — `.toast-stack` / `.toast`

用途：全域提示（SSE 斷線重試失敗、儲存成功等），掛在 `base.html` 的 `#toast-stack`。
變體：`.toast-error`、`.toast-success`、`.toast-warning`。
Token 依賴：`--color-bg-panel`、`--color-error/success/warning-600`、`--border-color`、
`--radius-sm`、`--z-toast`。

## Form field — `.form-field` / `.form-input` / `.form-textarea` / `.form-select`

用途：所有表單輸入（LLM 設定、對話輸入框）。
視覺：透明底 + 1px hairline 邊框，focus 邊框轉 System Blue、無外光暈。
`.form-label`（標籤）、`.form-hint`（輔助說明／空狀態文字）。
Token 依賴：`--color-text-400/500/900`、`--color-primary-600`（focus 邊框）、
`--border-color`、`--radius-sm`。

## Code block — `.code-block` / `.code-inline`

用途：DDL 顯示（`bg-panel` 深底等寬字 + hairline 外框）、行內程式碼片段。
`.code-copy-btn` 標示複製按鈕容器。
Token 依賴：`--color-bg-panel/active`、`--color-text-700`、`--color-primary-300`、
`--border-color`、`--font-family-mono`、`--radius-sm/xs`。

## Sidebar — `.sidebar`

用途：全站主導覽（首頁／DB Agent／設定）。
視覺：最深層底色 `bg-sidebar`（tiered 最暗），項目 hover 圓角底 `bg-panel`、
選中 `bg-active` + 亮白字（Stitch「Sidebar Items」規格，無 active 邊條）。
子元件：`.sidebar-logo`（含 `.sidebar-logo-accent`）、`.sidebar-nav`、
`.sidebar-nav-link`（`.is-active` 狀態）、`.sidebar-footer`。
Token 依賴：`--color-bg-sidebar/panel/active`、`--color-text-400/500/900`、
`--color-primary-600`、`--border-color`、`--layout-sidebar-width`、`--radius-sm`。

## Diff 標記 — `.diff-tag`

用途：需求確認頁「與現有 DB 差異」比對（新增／刪除／變更／不變）。
變體：`.diff-tag-new`、`.diff-tag-dropped`、`.diff-tag-modified`、`.diff-tag-same`。
`.diff-item` 為單筆差異列容器。
Token 依賴：`--color-success-100/500`、`--color-error-100/500`、`--color-warning-100/500`、
`--color-bg-panel`、`--radius-xs`。

## DB Agent 抽屜 — `.agent-drawer-toggle` / `.agent-drawer-panel`

用途：全站右下角浮動按鈕展開的 DB Agent 對話抽屜（`app/web/static/js/lib/drawer.js`
動態插入，/agent 完整頁不掛載）。
子元件：`.agent-drawer-header`（標題列 + 關閉鈕）、`.agent-drawer-messages`（訊息串）。
Token 依賴：`--color-primary-600/700`、`--color-bg-panel`、`--border-color`、
`--radius-full/lg`、`--z-dropdown`、`--space-*`。

## Markdown 文件內容 — `.doc-markdown`

用途：文件查閱頁／審查頁的 Markdown 純文字渲染（pre-wrap，不做 HTML 轉換）。
Token 依賴：`--font-size-base`、`--line-height-normal`、`--color-text-900`。

## 待審變更請求項目 — `.change-request-item`

用途：DB Agent 頁「待審變更請求」面板的單筆提案（DDL 預覽 + 核准/駁回按鈕）。
Token 依賴：`--border-color`、`--space-2/3`。

## tables_ready 橫幅 — `.chat-tables-ready-banner`

用途：需求收集對話頁收到 `tables_ready` 時插入的「前往確認頁」提示橫幅。
Token 依賴：`--color-accent-100/500`、`--color-primary-300`、`--radius-sm`、`--space-2/3/4`。

---

## 版面（`pages.css`，非元件，記錄供對照）

版面採 Stitch「Fixed-Grid Pro App 三欄 shell」：sidebar 240px
（`--layout-sidebar-width`）／主區流動／chat 與 agent 頁右欄視為 inspector
（`--layout-inspector-width` 300px，1px hairline 分隔 + 獨立捲動 + 細捲軸）。

`.app-shell`、`.main-content`、`.topbar`、`.page-content` 為 base.html 共用外殼；
`.page-chat-layout`、`.page-confirm-layout`、`.page-agent-layout` 為各頁的 grid 兩欄配置；
`.page-index-*`、`.page-docs-toolbar`、`.review-*`、`.settings-*` 為各頁專屬區塊排版；
`.page-agent-sidebar`（agent 頁側欄堆疊）、`.agent-turn-card`、`.docs-extra-row`、
`.settings-db-row`、`.settings-activity-row` 為第三波接線新增的區塊排版。

## data-* 綁定慣例

互動元素一律使用 `data-action`（觸發何種行為）與 `data-target`（指向哪個資料/DOM 節點），
JS（`app/web/static/js/pages/*.js`）只依賴這兩個 attribute，不依賴任何視覺 class 名稱——
換皮膚（Stitch 置換）時只要保留 `data-*`，class 名稱可任意調整。
