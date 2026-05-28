# SQL Agent 維運 Runbook

## 適用範圍

本文件描述 SQL Agent 在目前 Flask + JSON session store 架構下的基本維運方式。

## 重要目錄

| 位置 | 用途 | 是否應 commit |
|---|---|---|
| `.env` | Pensieve API token、empno、API URL | 否 |
| `data/` | Web session JSON 持久化資料 | 否 |
| `output/` | CLI 輸出文件 | 否 |
| `logs/system.log.jsonl` | 系統事件日誌 | 否 |

## 環境變數

| 變數 | 必填 | 說明 |
|---|---|---|
| `PENSIEVE_TOKEN` | 是 | Pensieve API token |
| `PENSIEVE_EMPNO` | 是 | 員工編號 |
| `PENSIEVE_URL` | 否 | Pensieve API endpoint |
| `PENSIEVE_BUILDING` | 否 | Flow 名稱，預設 `question` |
| `PENSIEVE_VERIFY` | 否 | SSL 憑證驗證 |
| `SQL_AGENT_LOG_DIR` | 否 | system log 輸出目錄，預設 `logs/` |

## 啟動檢查

```bash
pip install -r requirements.txt
pytest tests/ -v
python app.py
```

瀏覽器開啟：

```text
http://localhost:5000
```

## 常見問題排查

### 1. API 回傳 `internal server error`

檢查：

1. `logs/system.log.jsonl` 是否有 `api_error`。
2. Flask console 是否有 traceback。
3. `.env` 是否缺少 Pensieve 設定。

### 2. 文件產出卡住

檢查：

1. `data/{session_id}.json` 的 `generation_status`。
2. `logs/system.log.jsonl` 是否有 `generation_file_failed`。
3. Pensieve API 是否可連線。

### 3. DB 匯入失敗

檢查：

1. DB 連線字串是否正確。
2. PostgreSQL 是否允許目前主機連線。
3. `db_schema` 是否正確，預設為 `public`。
4. `logs/system.log.jsonl` 是否有 `db_import_failed`。

## 備份與還原

目前 session 資料存放於 `data/`，正式部署時至少需備份：

```text
.env
-data/
-logs/
```

建議每日備份 `data/`，並在版本發布前手動保留一份壓縮檔。

## 發布前 Smoke Test

每次部署前至少確認：

```text
□ 首頁可開啟
□ 可建立 design session
□ 可送出空訊息並得到 JSON error
□ 可完成一筆簡單資料表設計並進入確認頁
□ 可產出四份文件
□ 可下載 zip
□ review mode 可匯入測試 DB 或明確回報錯誤
□ logs/system.log.jsonl 有事件寫入
□ pytest tests/ -v 通過
```

## 事件日誌格式

每行是一筆 JSON：

```json
{
  "timestamp": "2026-05-28T00:00:00+00:00",
  "event_type": "generation_finished",
  "context": {
    "session_id": "..."
  }
}
```

敏感欄位會遮蔽：

```json
{"db_url": "***REDACTED***"}
```
