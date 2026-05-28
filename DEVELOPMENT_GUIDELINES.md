# SQL Agent — 網頁平台開發準則

本文件依據大型網頁平台開發準則制定，適用於 SQL Agent 專案的所有後續開發、維護與擴充工作。

---

## 一、專案定位

| 項目 | 說明 |
|---|---|
| 專案名稱 | SQL Agent — 資料庫建檔管理 Agent 系統 |
| 核心功能 | 對話式 AI 收集資料表設計需求，自動產出規格書、ER Diagram、DDL、安全規劃 |
| 技術棧 | Python 3.11 / Flask / Jinja2 / Vanilla JS / PostgreSQL |
| AI 串接 | Pensieve API（內部企業 LLM 閘道） |
| 使用族群 | 資料庫設計者、後端工程師、資訊部門 |

---

## 二、開發流程規範

### 2.1 需求確認（每次功能開發前必須完成）

- 明確說明這個功能要解決什麼問題
- 定義完成標準（什麼情況算做完）
- 確認是否影響現有 Session 資料格式（影響則需 Migration 或相容處理）
- 確認是否影響現有 API 回傳格式（影響則需版本協商）

### 2.2 實作前確認清單

```
□ 這個改動最小範圍是什麼？
□ 是否需要修改 session 資料結構？
□ 是否需要新增 API endpoint？
□ 是否影響前端 JS 邏輯？
□ 是否需要新增測試？
```

---

## 三、架構邊界

### 3.1 各層職責

| 層 | 位置 | 職責 | 不應做的事 |
|---|---|---|---|
| Flask Routes | `app.py` | URL routing、請求解析、回應格式 | 商業邏輯、直接存取 DB |
| Web Layer | `web/` | Session 管理、文件產出協調、DB 結構擷取 | 直接呼叫 AI API |
| Agents | `agents/` | AI 對話邏輯、需求擷取、文件撰寫 | 直接存取 Session 檔案 |
| Models | `models/` | 資料結構定義（dataclass） | 含商業邏輯 |
| Utils | `utils/` | HTTP 封裝、檔案 I/O | 含狀態 |
| Templates | `templates/` | HTML 結構與 Jinja2 渲染 | 商業邏輯判斷 |
| Static JS | `static/js/` | 頁面互動、API 呼叫 | 直接操作 Session 資料 |

### 3.2 Session 資料結構異動規則

Session 存放於 `data/{session_id}.json`，欄位新增必須：
1. 在 `web/session_store.py` 的 `create_session()` 加上預設值
2. 舊資料讀取時用 `.get("欄位", 預設值)` 防止 KeyError
3. 在本文件「Session 欄位定義」表格更新

---

## 四、API 設計規範

### 4.1 命名規則

```
GET    /api/sessions                           # 列表
POST   /api/sessions                           # 新增
GET    /api/sessions/<id>                      # 單筆讀取
POST   /api/sessions/<id>/messages             # 子資源操作
POST   /api/sessions/<id>/confirm              # 動作（動詞）
GET    /api/sessions/<id>/outputs              # 子資源讀取
GET    /api/sessions/<id>/outputs/zip          # 衍生資源
```

### 4.2 回應格式

成功：直接回傳資料物件，不包 `{ data: ... }` 外層。

錯誤：統一格式
```json
{ "error": "清楚描述問題的訊息" }
```

HTTP 狀態碼：
- `200` 讀取成功
- `201` 建立成功
- `400` 請求參數錯誤
- `404` 資源不存在
- `500` 伺服器錯誤（需記錄 Log）

### 4.3 禁止事項

- API 不得直接回傳 Python Exception 訊息給前端
- 分頁：超過 100 筆的列表 API 必須支援分頁（`page` / `per_page`）
- 大型文字欄位（如 AI 產出文件）不得在列表 API 中回傳，只在單筆 API 回傳

---

## 五、Session 欄位定義

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | str | UUID，Session 唯一識別 |
| `title` | str | 使用者輸入的設計標題 |
| `mode` | str | `"design"` 或 `"review"` |
| `phase` | str | `collecting` / `confirming` / `generating` / `done` |
| `messages` | list | 對話紀錄 `[{role, content, created_at}]` |
| `tables` | list | 設計完成的 TableSpec（JSON） |
| `context_tables` | list | 匯入的現有 DB 結構 |
| `context_text` | str | 現有 DB 的文字描述（傳給 AI） |
| `key_points` | list | AI 萃取的需求摘要 |
| `outputs` | dict | 已產出文件 `{"filename": "content"}` |
| `generation_status` | dict | 每個文件的產出狀態 |
| `table_versions` | list | 設計版本快照 |
| `created_at` | str | ISO 8601 建立時間 |
| `updated_at` | str | ISO 8601 最後更新時間 |

---

## 六、前端開發規範

### 6.1 JS 檔案職責

每個頁面對應一個 JS 檔案，僅處理該頁面的邏輯：

| 檔案 | 對應頁面 | 主要職責 |
|---|---|---|
| `home.js` | 首頁 | 專案列表、新增 Session |
| `chat.js` | 對話頁 | 訊息發送、AI 回應渲染、Markdown 渲染 |
| `confirm.js` | 確認頁 | Schema 預覽、Diff 顯示、版本還原、確認產出 |
| `docs.js` | 文件頁 | 文件輪詢、Mermaid 渲染、SQL 語法高亮、下載 |
| `review.js` | 審查頁 | 審查報告渲染、進度輪詢 |

### 6.2 API 呼叫規範

```javascript
// 標準 POST 模式
const res = await fetch('/api/...', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
});
const data = await res.json();
if (!res.ok) {
    // 顯示 data.error 給使用者
    return;
}
```

### 6.3 UI 互動規範

- 所有非同步操作（API 呼叫）必須有 Loading 狀態，避免使用者重複點擊
- 錯誤訊息需顯示在畫面上，不得只 `console.error`
- 輪詢（polling）間隔不得短於 2 秒，完成後必須停止
- 長文字（AI 產出文件）使用 `marked.js` 渲染 Markdown

---

## 七、測試規範

### 7.1 測試位置

所有測試放在 `tests/`，檔名以 `test_` 開頭。

### 7.2 測試覆蓋要求

| 類型 | 對象 | 說明 |
|---|---|---|
| 單元測試 | `agents/`、`web/`、`models/`、`utils/` | 純邏輯，不需 API 連線 |
| 整合測試 | Flask routes | 使用 `app.test_client()` |
| 資料測試 | Session 資料解析 | 確認 JSON 格式與欄位正確 |

### 7.3 測試執行

```bash
pytest tests/ -v
```

新功能上 PR 前必須通過所有測試。

---

## 八、Agents 開發規範

### 8.1 AI 呼叫原則

- 所有 AI 呼叫透過 `utils/client.py` 的 `PensieveAPI`，不得直接使用 HTTP 套件
- Prompt 文字存放在 `prompts/` 目錄，不得硬寫在 Python 程式碼中
- AI 回應需有錯誤處理，API 失敗不得讓整個 Session 崩潰

### 8.2 Interviewer 回傳規範

`interviewer.chat()` 回傳 3-tuple：`(reply_text, tables, summary)`
- `tables` 為 `None` 表示需求仍在收集中
- `tables` 非 `None` 表示需求收集完成，進入確認階段
- `summary` 為需求摘要清單（`list[str]`）

### 8.3 Writer 並行產出

`web/generation_worker.py` 以 `ThreadPoolExecutor` 並行執行四個 Writer，各 Writer 必須：
- 是 pure function（輸入 tables → 輸出文字）
- 不依賴 Session 狀態
- 失敗時拋出例外，由 worker 捕捉並更新 `generation_status`

---

## 九、錯誤處理與 Log 規範

### 9.1 必須記錄的錯誤

- AI API 呼叫失敗（包含 HTTP 狀態碼與回應）
- DB 連線失敗（不含密碼）
- 文件產出失敗（包含哪個 Writer 失敗）
- Session 檔案讀寫失敗

### 9.2 不得洩漏至前端的資訊

- Python Exception traceback
- 資料庫連線字串（含密碼）
- API Token

### 9.3 Log 格式

```python
import logging
logger = logging.getLogger(__name__)
logger.error("文件產出失敗 session=%s writer=%s error=%s", session_id, writer_name, str(e))
```

---

## 十、版本管理規範

### 10.1 Branch 命名

| 類型 | 命名 | 說明 |
|---|---|---|
| 功能開發 | `feat/<簡述>` | 新功能 |
| Bug 修正 | `fix/<簡述>` | 問題修復 |
| 文件更新 | `docs/<簡述>` | 僅文件變更 |
| 重構 | `refactor/<簡述>` | 不改功能的結構調整 |

### 10.2 Commit 訊息規範

```
<type>: <描述>（中文或英文均可）

type: feat / fix / docs / refactor / test / chore
```

範例：
```
feat: 新增 Schema Diff 視覺化顯示
fix: 修正版本還原後 phase 未重設問題
```

### 10.3 上線前檢查清單

```
□ pytest tests/ -v 全部通過
□ 手動測試設計模式完整流程（對話 → 確認 → 產出 → 下載）
□ 手動測試審查模式完整流程
□ 確認 .env.example 是否需要更新
□ 確認 requirements.txt 是否需要更新
□ 舊的 Session 資料格式是否仍可正常讀取
```

---

## 十一、禁止事項（紅線）

以下行為任何情況下都不允許：

1. **在程式碼中硬寫 API Token 或密碼**（一律使用 `.env`）
2. **在 AI Prompt 中包含真實使用者資料**
3. **將 `data/` 目錄的 Session 檔案提交到 git**
4. **直接在 `main` branch 開發**（必須開 branch 再 PR）
5. **跳過測試直接部署**
6. **靜默吞掉例外**（`except: pass` 類型的寫法）

---

## 十二、待辦與已知限制

| 項目 | 說明 | 優先度 |
|---|---|---|
| 並發安全 | 多個請求同時修改同一 Session 時有 race condition 風險，目前依賴 threading.Lock 局部保護 | 中 |
| Session 清理 | `data/` 目錄的 Session 檔案無自動清理機制 | 低 |
| 分頁 | `/api/sessions` 目前無分頁，Session 多時效能會下降 | 低 |
| 錯誤追蹤 | 目前無集中式錯誤追蹤（如 Sentry） | 低 |
