# 本機啟動與實測 Troubleshooting

本文件整理「在本機把 SQL Agent v2 平台實際跑起來、連上內網 LLM gateway 做功能測試」
過程中會遇到的問題與解法。內容依實際排錯順序編排，每一項都含：**症狀 → 根因 →
診斷方法 → 解法 → 驗證**。

> 適用情境：本機開發／內網 gateway 實測（SQLite + 自簽憑證 gateway + 公司 proxy 環境）。
> 正式部署請另見 [`deployment.md`](deployment.md)。

---

## 0. 正確的啟動順序（照做可略過大部分問題）

```powershell
# 1. 安裝依賴（含 dev）
pip install -e ".[dev]"

# 2. 建立資料表（首次或 schema 變更後必跑）
alembic upgrade head

# 3. 設定 LLM gateway（見 .env 範例，第 3~5 節）
#    並確保內網 IP 繞過 proxy
$env:NO_PROXY="10.10.23.120"

# 4. 啟動
uvicorn app.main:app --reload

# 5. 驗證
#    http://127.0.0.1:8000/healthz   → {"status":"ok"}
#    http://127.0.0.1:8000/docs      → API 文件
#    http://127.0.0.1:8000           → 前端七頁
```

**兩個貫穿全文的鐵則：**

1. **改任何 `.env` / `LLM_*` 設定後，一定要重啟 uvicorn。** 設定在啟動時載入且有
   `@lru_cache`（見 `app/config.py`），不重啟不會生效。
2. **`alembic upgrade head` 與 `uvicorn` 必須指向同一個 `DATABASE_URL`。** 本機用預設
   SQLite 時，只要都在專案根目錄執行即為同一個檔案。

---

## 1. `no such table: jobs`（資料表未建立）

### 症狀
啟動 uvicorn 後 worker poll loop 持續噴：

```
sqlite3.OperationalError: no such table: jobs
worker_poll_error
```

### 根因
只啟動了 uvicorn，沒有先跑 Alembic migration。SQLite 檔案會被自動建立，但裡面是空的、
沒有任何資料表。

### 解法

```powershell
alembic upgrade head
```

### 驗證

```powershell
alembic current      # 應顯示 0002 (head)
alembic history      # 應看到 0001_initial、0002_refresh_tokens
```

---

## 2. `Could not parse SQLAlchemy URL`（DATABASE_URL 空值）

### 症狀
執行 `alembic upgrade head` 時：

```
sqlalchemy.exc.ArgumentError: Could not parse SQLAlchemy URL from given URL string
```

### 根因
`DATABASE_URL` 被設成**空字串**（或只有空白）。空字串無法被 SQLAlchemy 解析。最常見是
在 `.env` 裡留了一行空的 `DATABASE_URL=`，它會**覆蓋掉** `app/config.py` 的預設值
`sqlite+aiosqlite:///./data/app.db`。

> 注意：`.env` 檔的值與 OS 環境變數都會覆蓋預設值，且 OS 環境變數優先權高於 `.env`。

### 診斷方法

```powershell
python -c "from app.config import get_settings; print(repr(get_settings().database_url))"
```

- 印出 `''` → 就是空值害的。
- 印出 `'sqlite+aiosqlite:///./data/app.db'` → 問題在別處。

### 解法（擇一）
- **A（最乾淨）**：刪掉 `.env` 裡那行 `DATABASE_URL=` —— 本機開發不需要它，會用預設 SQLite。
- **B**：補回值 `DATABASE_URL=sqlite+aiosqlite:///./data/app.db`。
- **C**：若 `.env` 沒問題，檢查是不是設了空的 OS 環境變數：
  ```powershell
  echo $env:DATABASE_URL           # PowerShell
  Remove-Item Env:DATABASE_URL     # 清掉
  ```

---

## 3. `llm_call_connection_error` — TLS 憑證信任失敗

### 症狀

```
llm_call_connection_error
LLMError - llm 連線失敗：Connection error.
```

用 PowerShell 內建 `curl`（其實是 `Invoke-WebRequest`）測 gateway 時：

```
curl : 基礎連接已關閉: 無法為 SSL/TLS 安全通道建立信任關係。
```

### 根因
內網 gateway 使用**自簽憑證**，用戶端預設不信任它。連線其實有連到伺服器（TLS 握手已開始），
只是憑證驗證被擋下。

### 診斷方法
用**真正的** curl（`curl.exe`，支援 `-k` 跳過憑證驗證）：

```powershell
curl.exe -k https://10.10.23.120:4231/public/kits/openai/v1/models
```

`-k` 之後連得到（有回應）→ 確認純粹是憑證信任問題。

### 解法
`.env` 設 `LLM_VERIFY=false`。`app/llm/provider.py` 在 `verify=False` 時會改用
`httpx.AsyncClient(verify=False)` 跳過憑證驗證。

### 驗證

```powershell
python -c "from app.config import get_settings as g; print('verify =', g().llm_verify)"
# 應為 False
```

---

## 4. `Connection error` — LLM_BASE_URL 路徑重複

### 症狀
`verify` 已是 false、gateway 用 curl 測得通，但 app 仍報 `Connection error`。

### 根因
`LLM_BASE_URL` **多接了 `/chat/completions`**。openai SDK 會自動在 base_url 後面補上
`/chat/completions`，所以實際打的網址路徑重複：

```
設定：https://10.10.23.120:4231/public/kits/openai/v1/chat/completions
實打：https://10.10.23.120:4231/public/kits/openai/v1/chat/completions/chat/completions
                                                    └── 你設的 ──┘└─ SDK 自己加 ─┘
```

### 判斷訣竅
用 curl **成功打通的完整端點**，把結尾的 `/chat/completions` 拿掉，剩下的才是 base_url。
- curl 用：`.../v1/chat/completions`
- base_url 應為：`.../v1`

### 解法
`.env`：

```
LLM_BASE_URL=https://10.10.23.120:4231/public/kits/openai/v1
```

---

## 5. `Connection error` — httpx 走了系統 Proxy（內網 IP 繞不過）

### 症狀
base_url 正確、verify=false、key 有設，**同一台機器** `curl.exe -k` 打得通，但 app 仍
`Connection error`。

### 根因
Python 的 httpx **預設會讀 Windows 系統 Proxy 設定**（`trust_env=True`，會抓
WinINET/登錄檔的公司 proxy）。內網 IP（如 `10.10.23.120`）的請求被送去 proxy，而 proxy
到不了內網機器 → 連線失敗。`curl.exe` 預設不讀登錄檔 proxy，所以直接連就通 —— 這正是
「curl 通、Python 不通」的原因。

### 診斷方法

```powershell
# 看 Python 偵測到的 proxy
python -c "import urllib.request; print(urllib.request.getproxies())"

# 對照測試：關掉讀系統 proxy 就會通
python -c "import httpx; print('trust_env=False:', httpx.get('https://10.10.23.120:4231/public/kits/openai/v1/models', verify=False, trust_env=False).status_code)"
python -c "import httpx; print('trust_env=True :', httpx.get('https://10.10.23.120:4231/public/kits/openai/v1/models', verify=False).status_code)"
```

第一行成功、第二行卡住 → 確認是 proxy。

### 解法
把 gateway IP 加進 `NO_PROXY` 例外清單（httpx 遵守此變數）：

```powershell
# 當前視窗（暫時）
$env:NO_PROXY="10.10.23.120"

# 永久：系統內容 → 環境變數 → 新增使用者變數 NO_PROXY = 10.10.23.120
```

> `NO_PROXY` 必須在**啟動 uvicorn 的同一個視窗**生效；診斷腳本也要在同視窗跑。

---

## 6. `POST /api/v1/llm/diagnose` 回 500 — 串流探針不相容（已修正）

### 症狀
平台可正常操作，但打能力探測端點回 `Internal Server Error`。uvicorn 終端機 traceback：

```
File "app/llm/provider.py", line 155, in _chat_stream
    if event.choices:
AttributeError: 'int' object has no attribute 'choices'
```

### 根因
兩層：
1. **gateway 串流不相容**：gateway 收到 `stream=True` 時回傳的資料，被 openai SDK 迭代出來
   是 `int` 而非標準 `ChatCompletionChunk`（沒有 `.choices`）。即此 gateway 的串流格式與
   OpenAI SSE 不相容。
2. **探針穩健性缺口**：`app/llm/capabilities.py` 的探針原本只
   `except LLMError`，接不到上述 `AttributeError`，於是例外往上冒 →
   `probe_all` → `diagnose` → 500。

探針的契約本應是「示範不出該能力就回 `False`」，所以應接住任何例外。

### 解法（已修正於 `app/llm/capabilities.py`）
**五個探針**（multi_turn / system_role / native_tools / json_schema / streaming）的
例外處理一律從 `except LLMError` 放寬為 `except Exception`，並記一行 warning log
（避免我方程式 bug 被靜默吞掉、無跡可查）。以 streaming 為例：

```python
async def probe_streaming(provider: "LLMProvider") -> bool:
    """stream=True，檢查是否能收到至少一個帶文字增量的 chunk。"""
    messages = [{"role": "user", "content": "請用一句話自我介紹。"}]
    try:
        stream = await provider.chat(messages, stream=True)
        chunks = [chunk async for chunk in stream]
    except Exception as exc:          # 原本是 except LLMError
        logger.warning("probe_streaming_failed: %r", exc)
        return False
    return any(chunk.delta for chunk in chunks)
```

不只修 streaming 的理由：五個探針契約相同，會回非標準串流的 gateway，其非串流回應
同樣可能觸發非 `LLMError` 例外，一次修齊。

修正後 `/diagnose` 會正常回傳 `streaming: false`，平台所有串流呼叫自動走非串流降級
（`single_chunk_stream`，前端 SSE 一次送完整段），功能不受影響。

---

## 附錄：能力探測（Capability Probing）怎麼測

能力探測判斷 gateway 支不支援五項能力，結果存進 `app_settings` 表；**不在每次請求執行**，
只在設定頁儲存連線或手動觸發時跑（見 `app/llm/capabilities.py`）。

| 探針 | 手法 | 判定 True |
|---|---|---|
| `multi_turn` | 三輪對話塞暗號 `SQLAGENT-7731`，第三輪問暗號 | 回覆含該暗號 |
| `system_role` | system 訊息要求固定回 `SYSMARK-OK` | 回覆含該標記 |
| `native_tools` | 給 `probe_echo` dummy tool 要它呼叫 | 回傳原生 `tool_calls` |
| `json_schema` | 帶 `response_format=json_schema` 要 `{ok:true}` | 回可解析成該結構的合法 JSON |
| `streaming` | `stream=True` | 至少收到一個帶文字增量的 chunk |

### 對真實 gateway 實測

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/v1/llm/diagnose   # 跑五項探針、存檔、回傳
curl.exe http://127.0.0.1:8000/api/v1/llm/health             # 只 ping + 回上次留存的 profile
```

> 某項回 `false` 未必是 bug —— 可能 gateway 真的不支援（常見於內網 gateway 無原生 function
> calling），偵測到就自動走降級路徑（`app/llm/adapters.py`）。

### 五項全 `false` 怎麼判讀

`/llm/health` 的 ping 只需要「單輪、無 system、無 tools、無 schema、非串流」——這正是
平台可正常操作的基本盤。五個探針各自**多加一樣東西**，如果 gateway 是「只收單輪 user
訊息」的薄包裝（內網常見），五項全 `false` 是**正確偵測，不是 bug**：平台會自動走五條
降級路（歷史合併單輪、system 內聯、文字模擬 tools、寬鬆 JSON 解析、非串流 SSE），功能
不受影響。

要區分「gateway 不支援」與「模型沒照做」與「我方程式問題」，看 uvicorn log 的
warning（每個探針 False 時都會留一筆）：

| log 訊息 | 意義 |
|---|---|
| `probe_*_failed: <例外>` | 呼叫本身失敗。`APIStatusError 4xx` → gateway 拒收該參數（不支援）；連線類 → 回到第 3~5 節排查 |
| `probe_multi_turn_false: 回應未含暗號 text='...'` | 呼叫成功但答不出暗號。看 text：答非所問 → gateway 丟了歷史；有理解但沒複述 → 模型指令跟隨弱 |
| `probe_system_role_false: 回應未含標記 text='...'` | 同上邏輯，看模型是回答了天氣（system 被丟棄）還是別的 |
| `probe_native_tools_false: 無原生 tool_calls text='...'` | gateway/模型不回原生 tool_calls（內網最常見） |
| `probe_json_schema_false: 回應非預期結構 text='...'` | response_format 被無視或模型輸出非目標 JSON |
| `probe_streaming_false: 未收到帶文字增量的 chunk` | 串流通了但沒有內容增量 |

**確認是模型指令跟隨弱（而非 gateway 限制）時的處理**：實際用對話功能驗證該能力可用後，
可用 `.env` 的 `LLM_FORCE_PROFILE` 強制覆蓋探測結果，例如只強制多輪與 system：

```
LLM_FORCE_PROFILE={"multi_turn": true, "system_role": true, "native_tools": false, "json_schema": false, "streaming": false}
```

> 強制為 true 但 gateway 實際不支援時，對應功能會直接出錯（不再降級），請逐項驗證後再開。
> 未列出的欄位視為 true。此變數只影響業務 provider（經 `app/services/llm_factory.py`），
> `/diagnose` 探測不受影響、仍量測 gateway 原始能力。

### 探測結果的生效範圍

探測出的 profile 存進 `app_settings` 後，所有業務呼叫點（對話、workbench NL2SQL、
文件產出、審查、DB Agent）都經由 `app/services/llm_factory.py` 讀取並套用降級轉接。
直接 `LLMProvider.from_settings()`（不帶 profile）只出現在探針與 health ping——
那兩處需要量測 gateway 原始能力。**新增 LLM 呼叫點時務必走 `llm_factory`，
否則探測結果不會生效**。

### 單元測試（不碰真實 gateway，用 respx mock）

```powershell
python -m pytest tests/llm/test_capabilities.py -v
```

---

## 附錄：LLM/模型相關設定總表

模型連線設定是 **`.env` 唯一入口**，UI 設定頁不管這塊（設定頁只管業務資料庫、活動紀錄、
平台自身 DB 概覽）。權威定義見 `app/config.py`。

### 必填
| 變數 | 說明 |
|---|---|
| `LLM_BASE_URL` | 到 `/v1` 為止，`/chat/completions` 由 SDK 補 |
| `LLM_API_KEY` | gateway API key |
| `LLM_MODEL` | 模型代號（curl 成功回應裡的 `"model"` 值） |

### 選填（有預設）
| 變數 | 預設 | 備註 |
|---|---|---|
| `LLM_VERIFY` | `false` | 自簽憑證維持 false |
| `LLM_TIMEOUT` | `120.0` | 內網模型慢可調高（如 300） |
| `LLM_FORCE_PROFILE` | 空 | 除錯用，塞 JSON 可強制覆蓋能力探測結果 |

### 非 LLM_* 但影響連線
| 項目 | 說明 |
|---|---|
| `NO_PROXY` | 內網 gateway IP 需加入，否則 httpx 走系統 proxy 連不上（見第 5 節）。**不是 `.env` 讀得到的，需設為 OS 環境變數。** |

### 範例 `.env`

```
LLM_BASE_URL=https://10.10.23.120:4231/public/kits/openai/v1
LLM_API_KEY=<你的key>
LLM_MODEL=<模型代號>
LLM_VERIFY=false
LLM_TIMEOUT=300.0
```

> 補充：`LLM_MODEL` 全平台共用一個（對話生成、DB Agent、能力探針同一顆模型），無「不同任務
> 不同模型」的設定。

---

## 問題點速查表

| # | 症狀 | 根因 | 解法 |
|---|---|---|---|
| 1 | `no such table: jobs` | 沒建表 | `alembic upgrade head` |
| 2 | `Could not parse SQLAlchemy URL` | `DATABASE_URL` 空字串 | 清掉 `.env` 空值或補回預設 |
| 3 | `Connection error`（TLS） | 自簽憑證 | `LLM_VERIFY=false` |
| 4 | `Connection error`（路徑） | `LLM_BASE_URL` 多接 `/chat/completions` | 只填到 `/v1` |
| 5 | `Connection error`（proxy） | httpx 走系統 proxy，內網 IP 繞不過 | 設 `NO_PROXY` |
| 6 | `/diagnose` 回 500 | gateway 串流不相容 + 探針只接 `LLMError` | 已修正：五探針一律 `except Exception` |
