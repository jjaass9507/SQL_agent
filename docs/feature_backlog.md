# 功能盤點與優先順序（三方評估整合）

本文件是一次針對「SQL Agent 還缺什麼功能」的結構化評估結果。三位評估者分別從
使用者工作流、Agent 能力、安全治理三個角度獨立盤點，交叉評論後由整合者裁決。

**文件性質**：待辦盤點與優先順序建議，不是已實作功能的說明。每一項的證據
（檔案:行號）都經整合者親自複驗，非轉述。

---

## 一、先講結論：這次盤點的主要發現不是「缺功能」

盤點前的假設是「功能有點少，該補什麼」。實際盤點後，最高優先的三件事**沒有一件是新功能**：

1. 一個已經破的安全護欄，而且現在就有人走得到（不需要新 UI）
2. 一批已經寫完、測試通過、但前端從未呼叫的後端能力
3. 稽核資料正在被每天丟棄——資料就在手上，只是沒往下傳

換句話說，**這個系統的問題不是做得太少，是已經做好的東西沒有接起來、
已經破的地方沒有補**。在補這些之前增加新功能，只會放大現有缺口。

---

## 二、第 0 級：現在就在流血

這一級不是「功能增強」，是「已經破損」。建議在任何新功能之前處理。

### 0-1　唯讀護欄擋不住藏在 SELECT 裡的寫入與 DoS　【已複驗】

`app/rules/sql_safety.py:92-119` 的 `check_read_only()` 只做三件事：語句數必須為 1、
第一個關鍵字不得是 `_FORBIDDEN_RE`（CREATE/DROP/ALTER/TRUNCATE/GRANT/REVOKE/
INSERT/UPDATE/DELETE/MERGE）名單內的動詞、外加兩個特例（data-modifying CTE、
`SELECT ... INTO`）。

以下語句**全部通過檢查**：

- `SELECT dblink_exec('dbname=prod user=... host=...', 'DELETE FROM orders')`
  — 若目標庫裝有 `dblink`/`postgres_fdw`，這會另開一條全新連線執行寫入，
  完全不受 `dbops.py:26-30` 設定的 `default_transaction_read_only=on` 約束
  （該設定只在當前連線的 session 層級生效）
- `SELECT pg_terminate_backend(...)` — 砍掉正式庫上的任意連線
- `CALL some_write_procedure()` — `CALL` 根本不在 `_FORBIDDEN_RE` 名單內
- `SELECT lo_export(...)` / `pg_read_file(...)` — 檔案系統讀寫

**為什麼是第 0 級**：這條路徑**完全不經 HITL 審批**（HITL 只保護 `propose_ddl`），
而且不是「未來開了 UI 才會有」的風險——`app/services/tool_registry.py:145-156`
的 `_tool_run_query` 走的是同一個 `check_read_only`，代表**現在**任何使用者都能
用一句自然語言請 DB Agent 查資料，讓模型自己（或被誘導）寫出這類語句。
`POST /api/sessions/{id}/query` 端點也已經上線，會打 API 的人今天就碰得到。

**認證擋不住這個**：`AUTH_ENABLED` 只限縮「誰能碰」，不改變「碰得到之後能做什麼」。
繞過發生時，行為主體是合法登入的內部員工。

**修法**：`sql_safety.py` 單一檔案、單一函式。在 `skeleton()`（已會剝除註解與字串
字面值，不會被註解或字串繞過）的結果上加兩個檢查——`^\s*(CALL|DO)\b`，
以及危險函式名 denylist（`dblink_exec`、`dblink_send_query`、`pg_terminate_backend`、
`pg_cancel_backend`、`lo_export`、`lo_import`、`pg_read_file`、`pg_write_file`、
`pg_reload_conf`、`set_config`、`copy` 相關等）。

成本：低。風險：高。

---

### 0-2　DB Agent 是全域共用的單一對話，所有人共讀彼此的查詢結果　【已複驗】

`app/services/agent_service.py:52-60`：DB Agent 的 session id 存在 `app_settings`
的固定 key，**全平台共用一條 transcript**，不是每人一條。

程式碼自己承認這件事——`start_new_conversation()` 的 docstring（`agent_service.py:63-71`）
寫著：

> 多人共用時前一個人的表名與查詢結果會一直留在 transcript 裡影響後續回答

目前唯一的緩解手段是使用者手動按「開新對話」。

**為什麼比原本認定的更嚴重**：這件事在盤點中被歸類為「記憶／上下文品質」問題
（業務術語會被 `_trim_to_budget` 砍掉而遺忘）。但它同時是**資料隔離**問題：

- A 同事請 agent 查了 `employees` 表，薪資與身分證欄位的**原始資料列**
  成為 tool observation 進入 transcript（`tool_registry.py:145-156` 不做任何遮罩）
- B 同事稍後在同一條 transcript 上提問，這些資料仍在上下文中，模型可能引用
- 這批資料同時被送往 `LLM_BASE_URL` 指向的服務。`app/config.py` 未限制該
  端點必須是內網私有部署

也就是說「敏感資料未遮罩」與「transcript 全域共用」兩個問題相乘，
影響範圍從「一次查詢」變成「一條所有人都在讀的共用上下文」。

**修法分兩段**，不必一次做完：

- 短期（低成本）：`_tool_run_query` 回傳前對欄位名比對敏感關鍵字清單
  （可沿用 `app/rules/metadata_checker.py` 既有的偵測規則），命中欄位的值一律
  換成 `"***"`。僅限 agent 工具路徑；workbench 人工查詢頁面的使用者本來就有
  權限看真實資料，不需遮罩。
- 中期：agent session 改為 per-user（`_get_or_create_agent_session_id` 加上
  使用者維度）。這會連帶解掉「業務術語被砍掉」的一半問題，因為單人 transcript
  的字數壓力遠低於全平台共用。

成本：短期低／中期中。風險：高。

---

### 0-3　稽核資料每天都在被丟棄　【已複驗】

`app/api/routers/changes.py:54-63` 的 approve / reject 端點透過 `require_admin_role`
依賴**已經取得 `current_user`**，但呼叫 `change_service.approve_change_request()` /
`reject_change_request()` 時**沒有把它傳下去**（`change_service.py:99,133` 的函式
簽名裡沒有這個參數）。結果：DDL 核准者的身分永遠不會進入 `activity_log`。

同樣地，`tool_registry.py:145-156` 的 `_tool_run_query` 全程沒有任何
`log_activity` 呼叫——DB Agent 查了什麼 SQL、查了哪張表，事後完全查不到。
（相比之下 `workbench_service.py:68-75` 至少記了回傳筆數，但沒記 SQL 內容、沒記 actor。）

**為什麼是第 0 級**：其他項目是「缺少某個能力」，這一項是**資訊正在永久流失**。
今天不補，今天發生的操作就永遠追不回來了；補得越晚，能追溯的歷史越短。
而且資料本來就在手上（`current_user` 就在 router 的區域變數裡），
只差往下傳一層。

**修法**：見第三節的 schema 裁決。

成本：低～中。風險：高。

---

## 三、整合者裁決：三組爭議

### 裁決一　新 UI 與安全修補的先後順序 → 順序成立，但理由要更正

爭議：把已實作的查詢面板開給使用者（見 1-1），是否必須等 0-1 補完。

三方最終一致同意「先補護欄」。但整合者要更正一個被反覆誤述的前提：

**這個漏洞不是新 UI 造成的。** `POST /api/sessions/{id}/query` 已經上線，
DB Agent 的 `run_query` 也走同一條檢查。新 UI 的作用是把風險從
「要會打 API 的人才碰得到」放大到「人人碰得到」——是**放大**，不是**製造**。

這個更正很重要，因為它改變了任務的性質：0-1 不是「開 UI 的前置條件」，
而是一個**獨立存在、應該立刻修的 bug**。就算永遠不開那個 UI，它也該修。

實務上：兩者成本都低，屬於「同一個發布週期內先後合併」，不是把 UI 擱置一季。

### 裁決二　一鍵送審是否要等鎖表問題修好 → 不用等，但要補風險揭露

爭議：降低送審摩擦（1-2）會不會加速撞上執行端的鎖表地雷（2-1）。

裁決：**兩者是獨立故障點，可平行進行。** 送審與執行之間隔著一道人工核准閘門，
送審變容易不代表核准會變隨便。

但附帶一個條件：`app/web/static/js/pages/agent.js` 的核准確認對話框目前列出的
facts 沒有任何一條提及「這個 DDL 含 `CREATE INDEX` 時會鎖表」。在送審入口變多
之前，這一行風險揭露應該補上——這是 UI 文案成本，不需要等 2-1 的完整方案。

### 裁決三　ActivityLog 該不該加欄位 → 先不加，但理由不是「models.py 不能動」

爭議：安全角度主張 `ActivityLog` 加 `actor` 欄位；Agent 角度主張全部塞進既有的
`detail_json`，理由是專案有「不得改動 `models.py`」的公約。

**整合者裁決：先用 `detail_json`，但兩邊的理由都不是決定因素。**

真正的決定因素是兩邊都沒提到的一件事——`app/repos/activity.py` 的
`list_activity()` **沒有任何過濾能力**，只有 `ORDER BY created_at DESC LIMIT 100`。
加一個 `actor` 欄位卻沒有查詢路徑，等於加了一個查不到的欄位。而稽核情境
（「上週那個 ALTER TABLE 是誰核准的」）真正需要的是**過濾 API**，
那個東西目前不存在，加不加欄位都一樣查不到。

所以正確的切法是：

- **現在**：把 actor、SQL 摘要、token usage 全部寫進 `detail_json`。零遷移。
  重點是**先讓資料被記錄下來**——資料形狀可以之後再改，丟掉的資料補不回來。
- **之後**：當稽核查詢真的成為需求時，欄位與過濾端點**一起**加。

三方原本分開提的三件事其實是同一件事，應合併為一次改動：

| 原提案 | 掛載點 | 內容 |
|---|---|---|
| 安全角度：actor | `changes.py` → `change_service` | 誰核准/駁回了 DDL |
| 安全角度：SQL 留痕 | `tool_registry.dispatch()` 後 | agent 執行了什麼 SQL |
| Agent 角度：usage | `provider.chat()` 後 | 這次花了多少 token |
| 工作流角度：session_id | `ChangeRequest` | 這個變更提案來自哪個設計 session |

前三者建議統一為一種 `agent_step` / `audit` 事件寫入 `detail_json`。
第四項（`ChangeRequest.session_id`）是唯一真正需要遷移的，可獨立評估。

---

### 附帶決定事項：「不得改動 models.py」公約已到期，需要人決定

`app/services/agent_service.py:7` 與 `app/services/interview_service.py:17` 的
docstring 都記載「`app/repos/models.py` 不在本階段可改動範圍」。這原本是 v2
重建計畫的分階段紀律。

但 README 顯示 **Phase 0–9 已全部完成**，而這個凍結已經讓至少兩個功能繞路實作
（interview 的 sticky 旗標「無合適欄位可存」只好塞進 `AppSetting`；
agent 的全域 session id 同樣塞進 `AppSetting`）。Alembic 遷移機制本身是完備的。

**這需要一個人為決定**：正式解除凍結（往後該加欄位就加），或明確重申
（往後所有狀態都走 `AppSetting`/`detail_json`）。目前的模糊狀態會讓每次
遇到 schema 需求都重新爭論一次，並持續累積繞路成本。整合者建議解除，
但這是專案負責人的決定，不是評估者能代替下的。

---

## 四、第 1 級：解鎖已經付出成本的價值

這一級的共同特徵：**後端已完成並有測試，只是沒有人接**。投報率最高。

### 1-1　把工作台五個端點接上前端　【已複驗】

`app/api/routers/workbench.py` 的五個端點全部實作完成、`tests/workbench/` 下有
對應測試：

| 端點 | 功能 |
|---|---|
| `POST /sessions/{id}/query` | 唯讀查詢 |
| `POST /sessions/{id}/explain` | 執行計畫 |
| `GET /sessions/{id}/schema-tree` | Schema 樹 |
| `POST /sessions/{id}/nl2sql` | 自然語言轉 SQL |
| `POST /sessions/{id}/validate-ddl` | DDL dry-run 驗證 |

`app/web/static/js/lib/api.js:29-33` 連五個端點常數都定義好了。但對整個
`app/web/static/js/` 做 grep，這五個常數**除了定義處之外零次出現**——
沒有任何頁面呼叫它們。

使用者現在的繞法：另開 DBeaver / psql，重新輸入連線字串（而設定頁存的連線字串
是加密的、不回傳前端，使用者手上通常也沒有原始密碼）。

建議順序：先做 query + explain（審查頁加一個查詢分頁），確認可用後再疊
nl2sql（把產生的 SQL 帶入查詢框，仍需人工按執行）與 validate-ddl
（確認頁編輯器旁加驗證按鈕）。

**前置條件：0-1 必須先合併。**

成本：低（純前端）。價值：高（每日高頻）。

### 1-2　文件頁／審查頁一鍵送審 + 待審提醒

目前設計模式產出 `03_ddl.sql`、審查模式產出 `06_review_fix.sql` 之後，
平台提供的唯一動作是「下載」（`docs.js` 的 action 只有 switch-tab / copy-code /
download-* / retry-generation / generate-extra；`review.js:101-114` 同理）。

要真的走 HITL，使用者必須離開文件頁，跑到獨立的 DB Agent 頁用自然語言
把需求重講一次，讓模型自行呼叫 `propose_ddl`。實務上的結果是：
使用者放棄走平台流程，直接把 DDL 拿去手動執行——正是這個平台想避免的情境。

同時，待審清單只在有人主動打開 DB Agent 頁時才載入
（`agent.js:101-139`，無輪詢、無 SSE、無通知）。同事晚上送出的提案，
管理員隔天不會收到任何提示。

**這兩件事必須綁在一起做**：只做送審不做提醒，等於把件丟進沒人看的信箱。

最小範圍：docs.html / review.html 各加一顆「提交變更審批」按鈕呼叫既有
`POST /change-requests`；`base.html` 的導覽列加一個待審數量 badge。

成本：低。價值：高。

---

## 五、第 2 級：確定性的地雷

### 2-1　`CREATE INDEX` 物理上做不到 CONCURRENTLY　【已複驗】

`app/rules/sql_safety.py:33-40` 的 `_ALLOWED_RE` 允許 `CREATE INDEX`。
但 `app/rules/ddl_executor.py:32-52` 用 psycopg2 預設連線（非 autocommit），
所有語句在同一個 `with conn.cursor()` 迴圈內執行、迴圈結束才 `conn.commit()`
——即**單一交易**。

PostgreSQL 規定 `CREATE INDEX CONCURRENTLY` 不能在交易區塊內執行。
因此這條路徑**物理上只能用會鎖表的方式建索引**：目標表拿到 `ACCESS EXCLUSIVE` 鎖，
期間所有讀寫全部卡住，直到索引建完或 `DDL_TIMEOUT_MS`（30 秒）逾時整批 rollback。

**這是唯一一個「流程完全正確、沒有任何繞過或惡意行為」也必然會發生的情境**：
DBA 正常提案、dry-run（跑在空的暫存 schema，秒過）通過、管理員正常核准，
然後正式服務被鎖住。allowlist 允許了一個 executor 無法安全執行的操作。

修法：`ddl_executor.py` 偵測語句含 `CONCURRENTLY` 時，該語句改用獨立
`autocommit=True` 連線單獨執行；失敗時補一次 `DROP INDEX CONCURRENTLY IF EXISTS`
清掉 PG 留下的 INVALID 索引。`sql_safety.py` 可對大表的 `CREATE INDEX`
加提示（先提示、不強制擋，避免誤傷小表場景）。

成本：中。風險：高。

### 2-2　`propose_ddl` 失敗就終止對話，模型沒機會自我修正

`app/services/agent_service.py:292-305`：只要工具名是 `propose_ddl`，不論成功或
回傳 `{"error": ...}`，都直接結束本回合。這與 `run_query`/`explain_query`
的行為不對稱——後兩者的錯誤**會**被當作 observation 餵回模型自我修正
（`agent_service.py:267-290`，有測試覆蓋）。

具體後果：使用者說「幫 users.email 建唯一索引」，dry-run 在真實資料庫撞到
既有重複值，使用者收到一句原始 Postgres 錯誤
（`duplicate key value violates unique constraint`）然後對話結束。
模型明明可以用 `run_query` 查出是哪些 email 重複、再建議先去重或改用非唯一索引，
但這一步永遠不會發生。

修法：把 terminal 判斷從 `call.name == "propose_ddl"` 改成
`call.name == "propose_ddl" and "error" not in capped`，並同步更新既有測試
`test_propose_ddl_failure_synthesizes_reply_without_extra_llm_call`。

**配套條件**（安全角度提出，整合者採納）：放行重試前應先有 2-4（查詢併發上限）
與 2-5（重複呼叫偵測），否則多位使用者的重試會疊加，反覆對正式庫開連線做
dry-run。`MAX_STEPS=8` 已鎖住單一對話的重試上限，但擋不住跨使用者的疊加。

成本：低（配套各自為低）。價值：高。

### 2-3　`get_schema` 無篩選、觀察值被硬砍在字元邊界

`tool_registry.py:121-128` 的 `_tool_get_schema` 拿回**全部**資料表的完整結構，
沒有任何篩選參數。`agent_service.py:170-175` 把結果序列化後硬砍在 4,000 字元
——**截斷點是字元數，不是表的邊界**。

300 張表時，使用者問「orders 表有哪些欄位」，`orders` 很可能整段落在截斷點之後，
模型拿到的是一份在 `orders` 出現前就被 `"...(截斷)"` 收尾的殘缺 JSON，
於是憑殘缺資料硬猜欄位（答錯），或反覆重查（見 2-5）。

專案裡已經有現成解法沒被複用：`app/rules/db_introspect.py:185-235` 的
`format_context()` 已寫好「依表數量分級降階」邏輯（>30 張表只給表名+欄位數+FK），
但只有 Interviewer 在用（`interview_service.py:174`），DB Agent 完全沒接。

修法：`get_schema` 加可選的 `tables: string[]` 參數；未指定且表數量多時複用
`format_context()`；`app/llm/prompts/agent.txt` 加一句引導模型先查表清單再縮小範圍。

成本：低～中。價值：高。

### 2-4　查詢無併發上限

`app/services/dbops.py:34,46`：`_run_sync` 每次呼叫都 `create_engine(...)` 新建
engine、用完 `dispose()`，沒有跨請求的連線池，也沒有任何併發上限。
唯一節流是 30 秒 statement timeout，對「短查詢但很多人同時打」無效。

多人同時在 workbench／DB Agent 對同一顆正式庫送查詢時，可能把該庫連線數頂到
上限，導致其他正式服務出現 `FATAL: too many connections`。

修法：`dbops.py` 加一個模組層級 `asyncio.Semaphore`（上限可用環境變數，
例如 10），`execute_query`/`explain_query` 呼叫 `_run_sync` 前 `async with`。
純 in-process，不需外部元件。

成本：低。風險：中（但它是 2-2 放行的前置條件）。

### 2-5　重複呼叫同一工具同參數無偵測

`agent_service.py` 主迴圈從未比對即將發出的 `call.name`/`call.arguments`
是否與本輪已執行過的相同。與 2-3 疊加後果明顯：截斷是**決定性**的
（同參數必定截在同一位置），模型重查只會拿到一模一樣的殘缺結果，
把 8 次預算燒完，使用者最後收到「已達到單回合最大工具呼叫次數」。

修法：迴圈內用一個 dict 記住本輪 `(name, args_json)` → `obs_text`；
命中時不重新執行工具，直接回上次結果並前綴一句提示，引導模型換路。

成本：低。價值：中（與 2-3 同根因，建議一起做）。

---

## 六、第 3 級：真實但可以晚一點

| 項目 | 說明 | 成本 |
|---|---|---|
| NL2SQL 強制 LIMIT | `_NL2SQL_SYSTEM` 未要求 LLM 加 LIMIT；`MAX_ROWS=200` 只截「取回」筆數，攔不住 DB 端已在做的全表排序 | 低 |
| MAX_STEPS 罐頭回覆 | `_MAX_STEPS_REPLY` 說「以下是目前已知的資訊」但後面什麼都沒有；應改成再打一次不帶工具的 LLM 呼叫做收尾 | 低 |
| 降級模式工具解析無重試 | `provider.py:151-164`，`emulate_tools` 解析失敗直接把半吊子 JSON 當最終答案；structured output 有重試機制可比照 | 低 |
| DDL 執行前 schema 快照 / 回滾建議 | 用 `information_schema` 輕量查詢記錄執行前欄位，機械對應出建議回滾語句供人工參考 | 中 |
| `remember_note` 業務術語記憶 | 讓使用者明確要求記住的業務定義存進 `AppSetting`，不參與 `_trim_to_budget` 裁剪 | 中 |
| 確認頁存檔樂觀鎖 | `PUT /sessions/{id}/tables-ddl` 無 `expected_version`，兩人同時編輯會靜默覆蓋 | 低～中 |

---

## 七、明確不建議現在做

| 項目 | 不做的理由 |
|---|---|
| Agent 品質評測框架 | 長期價值最高，但需要**持續**維護題庫，是一條產線不是一次修正。在第 0 級的破口補完前投入，順序不對。 |
| Session 分享唯讀連結 | 存取邊界未定義。若做成免登入公開連結，等同繞過 AUTH 把 schema 與查詢結果外流，與 0-2 的敏感資料問題直接衝突。要做必須先定義權限模型，不是加一顆按鈕。既有的 zip 下載雖然笨但走得通。 |
| 連線帳號最小權限檢查 | 屬於部署 checklist 而非產品功能，且是縱深防禦的第二層——第一層（0-1）都還沒補。 |

---

## 八、執行狀態

```
✅ 0-1  唯讀護欄改用允許清單（另實測發現 EXPLAIN ANALYZE <寫入> 可繞過）
✅ 0-2  agent 查詢結果敏感欄位遮罩（app/rules/sensitive_columns.py）
✅ 0-3  稽核留痕：誰核准了 DDL、agent 查了什麼 SQL
✅ 1-1  工作台端點接前端（查詢工作台四種模式）
✅ 2-1  CREATE INDEX CONCURRENTLY（原本物理上做不到）
✅ 2-2  propose_ddl 失敗不再終止對話
✅ 2-3  get_schema 分級降階 + tables/name_contains 篩選
✅ 2-4  查詢併發上限
✅ 2-5  重複呼叫偵測

⬜ 1-2  文件頁／審查頁一鍵送審 + 待審 badge
⬜ 2-6  DDL 執行前的 schema 快照與回滾建議
⬜ 第 3 級：NL2SQL 強制 LIMIT、MAX_STEPS 收尾、降級模式工具解析重試、
          remember_note 業務術語記憶、確認頁樂觀鎖

需要人為決定（不阻塞其他工作，但越早越好）
  ·  「不得改動 models.py」公約是否解除
     ——本輪新增的 session 標籤、常用問題、資料字典全部繞道 AppSetting，
       繞路成本持續累積
  ·  agent session 是否改為 per-user（0-2 的中期方案；目前全平台共用一條
     transcript，未遮罩前的查詢結果會成為下一個人的上下文）
```

### 測試防護

本輪同時建立了四層測試架構，讓上述修正不會回歸：

| 層 | 位置 | 抓什麼 |
|---|---|---|
| 架構規則 | `tests/architecture/` | 分層方向、LLM 呼叫點、錯誤訊息語言 |
| gateway 契約 | `tests/gateway/` | 五種 gateway 能力組合下的降級行為 |
| 瀏覽器煙霧 | `tests/e2e/`（`-m e2e`） | 「看起來像故障」的畫面 |
| 紀律 | `CLAUDE.md` 4.1／4.2 | 證明測試會失敗、哪一層該抓什麼 |

---

## 附錄：評估方法

三位評估者各自獨立讀程式碼提案，硬性要求每個提案附上檔案:行號證據，
並分別回答「使用者現在怎麼繞過這個缺口」（工作流角度）、
「不做會出什麼具體錯誤」（Agent 角度）、「具體事故情境」（安全角度）
——答不出來的提案視為假需求剔除。

第二輪交叉評論要求各自對三組衝突表態、點名對方的假需求、
並指出對方哪些提案該排在自己前面。

本文件所有標註【已複驗】及各節引用的檔案:行號，由整合者親自讀取原始碼確認，
未僅依評估者轉述。

### 過程中被修正的錯誤認定

- 「缺少 agent 執行軌跡可視化」——**不成立**。`app/web/static/js/pages/agent.js:22-37`
  的 `appendTraceStep` 已在側欄即時渲染工具呼叫軌跡。
- 「查詢面板會製造安全風險」——**不精確**。風險已存在於現有的 API 端點與
  agent 工具路徑，新 UI 是放大而非製造。
- 「`propose_ddl` 只驗語法」——**不成立**。`app/rules/ddl_validator.py:15-49`
  已對真實資料庫在 rollback 交易中執行 dry-run，這點做得比同類系統紮實。
- 「structured output 解析失敗無重試」——**不成立**。`provider.py:166-203`
  已有 `_STRUCTURED_RETRIES=2`。
