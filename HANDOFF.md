# HANDOFF — SQL Agent v2

> **接手這個專案的第一份文件。** 給沒有經歷這輪開發的人（或新的 AI 對話）用。
> 過程紀錄在 `docs/work_log_2026-08.md`，這裡只寫「要動手需要知道什麼」。
> 最後更新：2026-08，對應分支 `claude/sql-agent-features-qzpyem`。

---

## 0. 60 秒版

**這是什麼**：資料庫設計與查詢的協作平台。使用者用中文描述需求 → LLM 訪談 →
產出規格書 / ER 圖 / DDL → 人工審批後執行。另有一個 DB Agent 頁可以用中文問資料。
技術棧 FastAPI + SQLAlchemy 2.0 async + Alembic + Jinja2 + **原生 JS（無前端框架）**，
LLM 走 openai SDK 相容介面。

**現在在哪**：分支 `claude/sql-agent-features-qzpyem`（基於 `v2`，領先 23 個 commit，
72 檔 +6446/-129）。已完成系統面 9 項強化、使用者功能第一批全部與第二批 3 項，
並補上原本不存在的三層測試。`645 passed, 1 skipped` + `8 e2e passed`，ruff 全綠。
**尚未開任何 PR。**

**動手前必知的五件事**：

1. 推 `claude/sql-agent-features-qzpyem`，**不要推別的分支，不要擅自開 PR**。
2. 需要欄位就**加欄位、走 Alembic**（凍結公約已於 2026-08 解除，見 §6.1）。
3. **所有 LLM 呼叫必須經 `app/services/provider_factory.py`**，有架構測試在擋。
4. **使用者可見的錯誤訊息一律中文**，也有架構測試在擋。
5. 寫測試要**先看它紅**（`CLAUDE.md` §4.1）。這輪有三次「假的驗證」都是這樣抓到的。

LLM gateway 若是內網直連、不可經系統 Proxy，設 `LLM_TRUST_ENV=false`；預設 `true`
維持既有 Proxy 繼承行為。Proxy 拒絕會在 server log 保留可辨識的中文診斷。

**最快的上手動作**：

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q          # 應該 645 passed, 1 skipped
.venv/bin/ruff check .                 # 應該全綠
```

跑不出這個結果，就是環境有問題，不是程式有問題——先看第 3 節。

---

## 1. 先讀哪幾份文件

| 順序 | 檔案 | 為什麼要讀 |
|---|---|---|
| 1 | 本文件 | 環境、約束、待辦優先序 |
| 2 | `docs/work_log_2026-08.md` | 這輪做了什麼、為什麼這樣做；第八節記錄**被推翻的判斷** |
| 3 | `docs/feature_backlog.md` | 系統面缺口；第八節是執行狀態 |
| 4 | `docs/user_feature_backlog.md` | 使用者功能缺口；第六節是執行狀態 |
| 5 | `CLAUDE.md` §4.1 / §4.2 | 測試紀律：證明測試會失敗、哪一層該抓什麼 |
| 6 | `README.md` | 端點清單與測試分層 |

---

## 2. 必須遵守的約束

- **分支**：所有開發推 `claude/sql-agent-features-qzpyem`，未經明確許可不得推其他分支。
- **不得擅自開 PR**：使用者要求時才開。
- **基底是 v2**：使用者明確指定「要用現在 v2 這個下去改」。
- **`app/repos/models.py` 的凍結公約已解除**（2026-08，見 §6.1）——該加欄位就加，
  走 Alembic。既有的五個 `AppSetting` 繞路實作不必急著搬，判準見 §6.1。
- **不得關閉 TLS 驗證或 unset `HTTPS_PROXY`**（環境限制）。
- 錯誤訊息一律中文（有架構測試在擋，見 `tests/architecture/test_layering.py`）。

---

## 3. 環境重建

```bash
# Python 環境
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pip install playwright                    # e2e 用
.venv/bin/pip install -e ".[postgres]"              # 驗遷移／CONCURRENTLY 才需要

# 測試
.venv/bin/python -m pytest -q                       # 預設排除 e2e
PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers .venv/bin/python -m pytest -m e2e -q
.venv/bin/ruff check .

# 開發伺服器
DB_ENCRYPTION_KEY=$(python3 -c "print('ab'*32)") ADMIN_TOKEN=t \
  .venv/bin/python -m uvicorn app.main:app --port 8899 --host 127.0.0.1
```

驗證 `CREATE INDEX CONCURRENTLY`／`EXPLAIN` 這類**真的需要資料庫**的行為時，
容器內架 PostgreSQL 16：

```bash
useradd -m pg
mkdir -p /var/lib/pgdata && chown pg /var/lib/pgdata
mkdir -p /var/run/postgresql && chown pg /var/run/postgresql   # 少這步會 FATAL
su pg -c "/usr/lib/postgresql/16/bin/initdb -D /var/lib/pgdata -A trust -U postgres"
su pg -c "/usr/lib/postgresql/16/bin/pg_ctl -D /var/lib/pgdata -l /tmp/pg.log -o '-p 5433' start"
```

### 踩過的坑（會浪費你半天的那種）

- **`pkill -f <pattern>` 會殺掉 agent 自己的 shell（exit 144）**。改成：
  ```bash
  for p in $(ps -eo pid,args | grep "[m]ock_gateway.py" | awk '{print $1}'); do kill -9 $p; done
  ```
- **背景程序沒殺乾淨 → 新程序綁不到 port → 測試結果全部無效**。
  這輪有一整輪能力矩陣因此作廢（數字 143→169→188 沒歸零，代表全打到同一個 mock）。
  切換設定後**一定要驗證實際生效的是哪一個**——mock gateway 的 `/__stats` 端點就是為此加的。
- `tests/agent/conftest.py` 的 `db_session` 與 `client` 是**兩個獨立的 in-memory DB**；
  要驗路由層請用 `session_factory`。
- e2e 的 `live_server` 與直接寫 DB 可能不同源，測試前置一律走 API。
- Chromium 在 `/opt/pw-browsers/chromium`；CI 沒有這個路徑，
  `tests/e2e/conftest.py` 已處理（檔案不存在時 `executable_path=None`）。

---

## 4. 程式碼地圖（找東西從這裡開始）

**分層方向**：`app/api/routers` → `app/services` → `app/repos` → `app/repos/models`。
`app/rules`（純規則、無狀態）與 `app/llm`（純呼叫層）**不得依賴上層**——
`tests/architecture/test_layering.py` 在擋反向依賴。

| 想改什麼 | 去哪裡找 |
|---|---|
| 新增 / 修改 API 端點 | `app/api/routers/`（`sessions` / `agent` / `workbench` / `changes` / `outputs` / `settings` / `llm` / `auth`） |
| 業務流程、跨 repo 的協調 | `app/services/` |
| **LLM 呼叫** | `app/services/provider_factory.py`（唯一入口，見 §5.1） |
| 唯讀 SQL 護欄 | `app/rules/sql_safety.py`（允許清單，見 §5.2） |
| DDL 解析 / 驗證 / 執行 | `app/rules/ddl_parser.py`、`ddl_validator.py`、`ddl_executor.py` |
| 產出規格書 / ER 圖 / DDL 文字 | `app/services/writers/`、`app/rules/writers/` |
| DB Agent 的工具定義與派發 | `app/services/tool_registry.py` |
| DB Agent 的對話迴圈 | `app/services/agent_service.py` |
| 查詢工作台（後端） | `app/services/workbench_service.py` |
| 頁面 HTML | `app/web/templates/` |
| 頁面行為 | `app/web/static/js/pages/`（`index` / `chat` / `confirm` / `docs` / `review` / `agent` / `settings`） |
| 共用前端元件 | `app/web/static/js/lib/`（`api.js` 是所有端點常數的集中處） |
| 資料表定義 | `app/repos/models.py`（可改動，加欄位請走 Alembic——見 §6.1）；`RefreshToken` 例外地放在 `app/repos/users.py` |

**兩個容易踩的地雷**：

- `app/web/static/js/lib/api.js` 裡 `workbenchQuery`（session 範圍）與
  `workbenchDbQuery`（業務庫範圍）**刻意不同名**，取成同名會在物件字面值裡被覆蓋。
- `tests/agent/conftest.py` 的 `db_session` 與 `client` 是**兩個獨立的 in-memory DB**。
  要驗路由層請用 `session_factory`，不要用 `db_session` 寫完再打 API。

---

## 5. 這輪的架構決定（接手前要理解的四件事）

### 5.1 `provider_factory` 是所有 LLM 呼叫的唯一入口

`app/services/provider_factory.py` 負責載入 `CapabilityProfile` 再建 provider。
**任何地方都不准直接呼叫 `LLMProvider.from_settings()`**，唯二合法例外是
`app/api/routers/llm.py`（能力探針本身）與 `provider_factory` 自己。
`tests/architecture/test_layering.py` 用原始碼掃描在擋這件事。

原因：能力探測結果原本只有 DB Agent 在用，訪談與 nl2sql 對不支援 system role 的
gateway 會直接 500。

### 5.2 唯讀護欄改成允許清單

`app/rules/sql_safety.py`：開頭必須是 `SELECT|WITH|VALUES|TABLE|SHOW`，
`EXPLAIN` 視為透明包裝（會剝掉再檢查），另外全文掃描巢狀寫入動詞與危險函式
（`dblink*`／`setval`／`nextval`／`pg_*advisory*`／`pg_sleep*`／檔案系統／`set_config`…）。

原因：`EXPLAIN ANALYZE DELETE FROM orders` 會**真的執行**；`setval()` 回傳數字看起來像
讀取，實際改動序列值且回不去。

### 5.3 敏感欄位遮罩只套 agent 路徑

`app/rules/sensitive_columns.py` 的 `mask_result()` 套在 `_tool_run_query`，
**不**套在 workbench 人工查詢頁（人工查詢是使用者自己的資料，遮了反而沒用）。
這是「DB Agent 共用 transcript」的短期防護，不是根本解（見 §6.2）。

### 5.4 四層測試

| 層 | 抓什麼 | 位置 |
|---|---|---|
| unit / rules | 純邏輯、邊界 | `tests/rules/`、`tests/repos/` |
| API contract | 前端依賴的請求／回應形狀 | `tests/web/test_contract.py` |
| architecture | 跨檔案、沒人擁有的約定 | `tests/architecture/` |
| gateway contract | LLM gateway 降級時的行為 | `tests/gateway/` |
| browser smoke | 「看起來壞掉」 | `tests/e2e/`（`-m e2e`） |

**`CLAUDE.md` §4.1 的紀律務必遵守**：寫完測試要先看它紅，或事後把 bug 種回去確認變紅，
**並且要打開檔案確認 bug 真的種進去了**。這輪有三次驗證失誤都是這樣才抓到的：
- e2e ER 測試在只有一張表的 fixture 下永遠綠——bug 來自量測**關係路徑**，沒有連線就沒有路徑。
- 併發測試把上限改成 999 仍然通過——量到的是 asyncio 執行緒池，不是我的 semaphore。
  正確做法：`monkeypatch.setattr(dbops, "_query_slots", asyncio.Semaphore(2))`。
- 「摘要必須涵蓋所有表」這條斷言**本身就是錯的**，與 observation 預算矛盾。

---

## 6. 需要使用者拍板的事項

### 6.1 `models.py` 凍結公約 —— ✅ 已於 2026-08 由專案負責人解除

**現行規則：需要欄位就加欄位，走 Alembic 遷移。** 不再為了避開 `models.py`
把狀態塞進 `AppSetting`。

背景：這原本是 v2 重建計畫的分階段紀律（`models.py` 屬於已完成階段，不在當期
可改動範圍）。Phase 0–9 全部完成後這個理由就消失了，但公約沒有跟著撤銷，
於是累積出五個繞路實作。要注意的是那些繞路**在當時的規則下是對的選擇**，
現在只是不再需要那個規則——不是回頭認定它們寫錯了。

**加欄位的作法**（Alembic 機制完備，三個既有遷移可參考）：

```bash
.venv/bin/alembic revision -m "描述"      # 手寫 upgrade/downgrade，不要 --autogenerate
.venv/bin/alembic upgrade head
```

`downgrade()` 要真的寫得出來——測試環境是每次重建的 in-memory SQLite（走
`create_all`，**根本不會執行遷移**），所以遷移寫錯不會被任何測試抓到。
**遷移一定要手動對兩種資料庫各跑一次 upgrade → downgrade**，`0004` 就是這樣驗的：

```bash
# SQLite
DATABASE_URL="sqlite+aiosqlite:////tmp/mig.db" .venv/bin/alembic upgrade 0003
#   → 塞入測試資料 → upgrade head → 檢查 → downgrade 0003 → 檢查資料有回來
# PostgreSQL（.venv/bin/pip install asyncpg，並啟動第 3 節那個 pg）
DATABASE_URL="postgresql+asyncpg://postgres@127.0.0.1:5433/migtest" .venv/bin/alembic upgrade head
```

**兩種都要跑**，因為 JSON 欄位的行為不同：PostgreSQL 的驅動會把 `value_json`
解碼成 Python `True`，SQLite 回傳字串 `"true"`；而 `Uuid` 欄位在 SQLite 存成
無連字號的 32 字元 hex。`0004` 的讀寫都必須標註型別
（`sa.bindparam(type_=sa.JSON())`、`.columns(sa.column("id", sa.Uuid()))`），
不然來回一趟就會拼出跟原本不一樣的 key，或在 PostgreSQL 上型別錯誤。

**解除後仍然成立的兩件事**（不要當成「現在可以隨便改 schema」）：

- **既有欄位的破壞性變更仍需單獨評估**，特別是 `messages.role` 的
  CheckConstraint——放寬它要連帶回填既有 transcript 資料。
- **不是每個繞路都該搬回來。** 判準是**有沒有查詢需求**，不是「JSON 看起來很醜」。

五個繞路實作的現況與建議（都不急，沒有一個是壞掉的）：

| 繞路 | 現況 | 建議 |
|---|---|---|
| interview 的 sticky 旗標 | ✅ **已收回** `sessions.inject_db_context`（遷移 `0004`） | 完成。原本 key-per-session 會隨 session 數量無限長大，也不會跟著 session CASCADE 刪除 |
| `RefreshToken` 定義在 `app/repos/users.py` | 沿用同一個 `Base`，功能正常 | 搬回 `models.py` **不需要遷移**，純粹是類別定義換檔案。做很便宜，不做也沒代價 |
| session 標籤與釘選 | 單一 JSON | 等到「列出所有標成 PM-陳 的 session」需要 SQL、而不是全撈進記憶體過濾時再建表 |
| 常用問題 / 資料字典 | 單一 JSON | 同上。要跨資料庫統計「哪些問題最常被跑」才需要建表 |
| agent 全域 session id | 單一 `AppSetting` | **不必動**。它就是一個全域設定值，正是 `AppSetting` 該存的東西 |

另外 `ChangeRequest.session_id`（變更提案來自哪個設計 session）是
`docs/feature_backlog.md` 裁決三裡唯一真正需要遷移的項目，現在沒有理由再擋著。

### 6.2 DB Agent 是否改成 per-user

目前全平台共用一條 transcript，程式碼 docstring 自承
「前一個人的表名與查詢結果會一直留在 transcript 裡」。
已做敏感欄位遮罩當短期防護，根本解法是隔離。

### 6.3 Gemini API 金鑰

免費 OpenAI 相容 API 實測結果：**只有 Google Gemini 端點通得過本環境代理**，其餘全部 403。
真連線測試需要使用者提供金鑰：

```
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
LLM_API_KEY=<金鑰>
LLM_MODEL=gemini-2.5-flash
```

（我沒有代替使用者註冊任何帳號。）

---

## 7. 待辦（優先序未變）

### 系統面 — `docs/feature_backlog.md`

- **1-2 文件頁／審查頁一鍵送審 + 待審 badge**（兩者必須綁在一起做，只做一半沒有價值）
- **2-6 DDL 執行前的 schema 快照與回滾建議**
- 第 3 級：NL2SQL 強制 LIMIT、MAX_STEPS 收尾改為再打一次不帶工具的 LLM、
  降級模式工具解析重試、`remember_note` 業務術語記憶、確認頁樂觀鎖

### 使用者功能 — `docs/user_feature_backlog.md`

- **第二批剩餘**：紅旗逐條標記、退回機制（收斂版：待回應項目清單 + DDL 存檔連動清除退回標記）、淺色模式切換
- **第三批**：命名 inline 標示、查詢結果圖表、一句話摘要、版本逐欄位比較、
  稽核篩選匯出、上線前檢查清單、常用需求片段、複製表名圖示、查詢結果旁一鍵 EXPLAIN
- **暫緩**：每月自動更新（缺排程基礎建設）、文件分享連結（缺權限模型定案）

### 我沒能替使用者驗證的

- 驗收者「甲」要求測 **Confluence 收不收下載的 ER SVG**——本環境連不到 Confluence。
  請使用者自行測試，若不行再補 PNG 匯出。

---

## 8. 這輪動過的檔案（找東西用）

### 新增

| 檔案 | 作用 |
|---|---|
| `app/services/provider_factory.py` | LLM provider 唯一入口（§5.1） |
| `app/rules/sensitive_columns.py` | 敏感欄位偵測與遮罩 |
| `app/services/session_labels.py` | Session 標籤與釘選（存 `AppSetting`） |
| `app/services/saved_questions.py` | 常用問題 + 90 天核可效期 |
| `app/web/static/js/lib/query-workbench.js` | 四種查詢模式共用的結果區 |
| `app/web/static/js/lib/explain-plan.js` | 執行計畫樹狀渲染，Seq Scan 標紅 |
| `app/web/static/js/lib/schema-browser.js` | 表／欄位／註記三合一搜尋 |
| `tests/architecture/`、`tests/gateway/`、`tests/e2e/` | 三層新測試 |

### 大改

| 檔案 | 改了什麼 |
|---|---|
| `app/rules/sql_safety.py` | 允許清單 + 危險函式全文掃描（§5.2） |
| `app/rules/ddl_executor.py` | CONCURRENTLY 拆出主交易單獨執行、鎖警告、失效索引清理 |
| `app/services/tool_registry.py` | 稽核留痕、結果遮罩、`get_schema` 分級（表數超過 15 走摘要） |
| `app/services/agent_service.py` | 重複工具呼叫偵測、`propose_ddl` 失敗不再誤判為結束 |
| `app/services/workbench_service.py` | 業務庫直連查詢／EXPLAIN／schema 樹／nl2sql、DDL 語法驗證、資料字典 |
| `app/services/dbops.py` | `MAX_CONCURRENT_QUERIES` semaphore |
| `app/services/writers/diagram_writer.py` | 中文表名／欄位名的 mermaid 逸出 |
| `app/web/static/js/pages/agent.js` | 分頁、四模式、常用問題、資料字典 |

---

## 9. 驗收方式（如果要沿用）

這輪採用「三個 persona subagent 互相討論 + 逐階段驗收」：
角色互寄訊息（每人上限 4 則）直接辯論，而非各自向我回報。
這個方法換到轉述問答做不到的結果——五個提案合併成一個查詢工作台、
長出「可信度標記」、再延伸出「核可會過期」。

驗收者三次擋下發布，理由都成立，值得記住：
- 「我沒辦法信任下載的 SVG」——只給單一模式截圖就宣稱全部沒問題，等於要人用猜的驗收。
- 「文案在騙人」——按鈕寫「下載 Excel」但輸出 `.csv`。
- 英文錯誤訊息會被當成系統故障。

---

_本文件寫於 2026-08，以當時 `claude/sql-agent-features-qzpyem` 的分支狀態為準。_
_改動行為時請一併更新這裡與 `docs/work_log_2026-08.md`（`CLAUDE.md` §5 文件衛生）。_
