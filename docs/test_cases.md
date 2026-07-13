# SQL Agent — 測試案例清單

> **現況（v0.5）**：6 份 unit tests 覆蓋 CLI 層（client、file_writer、interviewer parser、models、orchestrator、writers）。  
> 本文件定義 **v1.0 測試目標**，包含 API 整合測試、並發測試、與 UAT 案例。

---

## 一、現有測試覆蓋範圍

| 測試檔案 | 覆蓋模組 | 測試重點 |
|---|---|---|
| `tests/test_client.py` | `utils/client.py` | API 回應解析（`_extract_text`）、無需 HTTP |
| `tests/test_file_writer.py` | `utils/file_writer.py` | 目錄建立、檔案寫入、UTF-8 |
| `tests/test_interviewer_parser.py` | `agents/interviewer.py` | `_parse_tables()` XML tag 解析、預設值防護 |
| `tests/test_models.py` | `models/schema.py` | `ColumnSpec` / `TableSpec` dataclass 欄位 |
| `tests/test_orchestrator.py` | CLI Orchestrator | 端到端 CLI 流程（mock API） |
| `tests/test_writers.py` | `agents/writers/*.py` | 各 Writer.generate() 呼叫（mock API） |

---

## 二、API 整合測試（待實作：`tests/test_api.py`）

使用 `flask.testing.FlaskClient`，不需真實 LLM 或 DB。

### TC-API-01：建立 Session — 設計模式

| 項目 | 內容 |
|---|---|
| **前置條件** | 無 |
| **操作** | `POST /api/sessions` `{"title": "訂單系統", "mode": "design"}` |
| **預期結果** | HTTP 201；回傳 `{id, title, mode, phase}`；`phase == "collecting"` |
| **驗證方式** | `assert resp.status_code == 201` + `assert data["phase"] == "collecting"` |

### TC-API-02：建立 Session — 缺少 mode 欄位（預設值）

| 項目 | 內容 |
|---|---|
| **操作** | `POST /api/sessions` `{"title": "無 mode 欄位"}` |
| **預期結果** | HTTP 201；`mode == "design"` |

### TC-API-03：取得不存在的 Session

| 項目 | 內容 |
|---|---|
| **操作** | `GET /api/sessions/00000000-0000-0000-0000-000000000000` |
| **預期結果** | HTTP 404 |

### TC-API-04：送出空白訊息

| 項目 | 內容 |
|---|---|
| **操作** | `POST /api/sessions/<id>/messages` `{"content": ""}` |
| **預期結果** | HTTP 400；`{"error": "content required"}` |

### TC-API-05：對非 collecting 階段送出訊息

| 項目 | 內容 |
|---|---|
| **前置條件** | phase = "generating" |
| **操作** | `POST /api/sessions/<id>/messages` `{"content": "hello"}` |
| **預期結果** | HTTP 400 |

### TC-API-06：Confirm 無資料表

| 項目 | 內容 |
|---|---|
| **前置條件** | session.tables = null |
| **操作** | `POST /api/sessions/<id>/confirm` |
| **預期結果** | HTTP 400；`{"error": "no tables to generate"}` |

### TC-API-07：重複 Confirm（防 double-submit）

| 項目 | 內容 |
|---|---|
| **前置條件** | phase 已為 "generating" |
| **操作** | 連發兩次 `POST /api/sessions/<id>/confirm` |
| **預期結果** | 第一次 200；第二次 400 |

### TC-API-08：取得版本列表

| 項目 | 內容 |
|---|---|
| **前置條件** | session 有 2 個版本快照 |
| **操作** | `GET /api/sessions/<id>/versions` |
| **預期結果** | HTTP 200；陣列長度 = 2；每項有 `version`、`created_at`、`table_count` |

### TC-API-09：還原不存在的版本

| 項目 | 內容 |
|---|---|
| **操作** | `POST /api/sessions/<id>/versions/999/restore` |
| **預期結果** | HTTP 404 |

### TC-API-10：下載 ZIP — 無輸出

| 項目 | 內容 |
|---|---|
| **前置條件** | outputs = {} |
| **操作** | `GET /api/sessions/<id>/outputs/zip` |
| **預期結果** | HTTP 400 |

### TC-API-11：Health Check

| 項目 | 內容 |
|---|---|
| **操作** | `GET /health` |
| **預期結果** | HTTP 200；`{"status": "ok", "version": "..."}` |

### TC-API-12：分頁參數

| 項目 | 內容 |
|---|---|
| **前置條件** | 存在 5 個 sessions |
| **操作** | `GET /api/sessions?limit=2&offset=0` |
| **預期結果** | HTTP 200；陣列長度 = 2 |

---

## 三、Session Store 並發測試（待實作：`tests/test_session_store.py`）

### TC-STORE-01：並發寫入不同 session — 無資料競爭

| 項目 | 內容 |
|---|---|
| **操作** | 10 個 thread 同時呼叫 `update_session()`，各操作不同 session |
| **預期結果** | 所有 session 最終狀態正確；無 JSON 損毀 |

### TC-STORE-02：並發寫入同一 session — Lock 保護

| 項目 | 內容 |
|---|---|
| **操作** | 5 個 thread 同時呼叫 `add_message(session_id, ...)` |
| **預期結果** | 最終 messages 陣列長度 = 5；無遺漏 |

### TC-STORE-03：`try_start_generation` 原子性

| 項目 | 內容 |
|---|---|
| **操作** | 3 個 thread 同時呼叫 `try_start_generation(session_id)` |
| **預期結果** | 只有 1 個 thread 回傳 True；其他回傳 False |

### TC-STORE-04：版本上限（最多 10 版）

| 項目 | 內容 |
|---|---|
| **操作** | 呼叫 `set_tables()` 12 次 |
| **預期結果** | `table_versions` 長度 = 10；保留最新 10 版 |

### TC-STORE-05：`restore_version` 版本不存在

| 項目 | 內容 |
|---|---|
| **操作** | `restore_version(session_id, 99)` |
| **預期結果** | 回傳 False；session 狀態不變 |

---

## 四、Interviewer Parser 測試（現有，TC-PARSER-*）

| 案例 | 輸入 | 預期 |
|---|---|---|
| TC-PARSER-01 | 完整 `<TABLE_SPECS>` JSON | 正確解析 `ColumnSpec` 所有欄位 |
| TC-PARSER-02 | `is_primary_key` 欄位缺失 | 預設 `False`，不拋 `KeyError` |
| TC-PARSER-03 | `references` 欄位缺失 | 預設 `None` |
| TC-PARSER-04 | LLM 回傳無 XML tag | 回傳 `None`（非 tables）|
| TC-PARSER-05 | `<REQUIREMENTS_SUMMARY>` tag | 正確解析摘要清單 |

---

## 五、Schema Diff 測試（待補充）

| 案例 | 說明 | 預期 |
|---|---|---|
| TC-DIFF-01 | 設計新增一個資料表 | `added_tables` 長度 = 1 |
| TC-DIFF-02 | 設計刪除一個欄位 | `modified_tables[0].removed_columns` 有對應項 |
| TC-DIFF-03 | 設計與現有完全相同 | 所有 diff list 為空 |
| TC-DIFF-04 | 欄位型態改變 | `modified_tables[0].changed_columns` 有對應項 |

---

## 六、UAT 驗收案例

### UAT-01：設計模式端到端流程

**角色**：SA  
**步驟**：
1. 首頁建立新 session（設計模式）
2. 輸入「我需要一個會員資料表，含姓名、Email、密碼、建立時間」
3. 回答 AI 追問（主鍵格式、Email 唯一性）
4. 確認頁核對 Schema
5. 點「確認，開始產出文件」
6. 等待四份文件產出完成
7. 下載 ZIP

**驗收標準**：
- [ ] AI 在 3 輪內追問完並回傳 Schema
- [ ] 確認頁正確顯示資料表與欄位
- [ ] 四份文件在 60 秒內全部完成
- [ ] ZIP 內含 4 個檔案，DDL 含 `CREATE TABLE`

### UAT-02：版本還原

**角色**：SA  
**步驟**：
1. 完成第一輪對話（取得 v1 Schema）
2. 返回補充需求，新增一個欄位（取得 v2 Schema）
3. 確認頁點「版本 1」→ 還原

**驗收標準**：
- [ ] 確認頁顯示兩個歷史版本
- [ ] 還原後 Schema 回到 v1 的欄位數

### UAT-03：審查模式端到端

**角色**：SA  
**步驟**：
1. 首頁選擇審查模式，填入 PostgreSQL 連線字串
2. 建立 session，等待分析

**驗收標準**：
- [ ] 60 秒內產出審查報告
- [ ] 報告含四段分析 + 整體評分
- [ ] 可下載 .md 報告

### UAT-04：匯入現有 DB 作為設計參考

**角色**：Dev  
**步驟**：
1. 建立設計 session 時填入 DB 連線字串
2. 在對話中提問現有表的命名規範
3. 確認頁查看 Schema Diff

**驗收標準**：
- [ ] 確認頁顯示「新增/刪除/變更欄位」差異
- [ ] AI 在對話中參考現有 DB 結構

---

## 七、測試執行指令

```bash
# 現有 unit tests
pytest tests/ -v

# API 整合測試（待實作）
pytest tests/test_api.py -v

# 並發測試（待實作）
pytest tests/test_session_store.py -v

# 全套執行 + 覆蓋率報告
pytest tests/ -v --cov=. --cov-report=term-missing
```

---

## 八、測試覆蓋目標

| 模組 | 目前覆蓋率 | v1.0 目標 |
|---|---|---|
| `utils/client.py` | 高 | ≥ 80% |
| `utils/file_writer.py` | 高 | ≥ 80% |
| `agents/interviewer.py` | 中（parser 部分）| ≥ 70% |
| `web/session_store.py` | 低 | ≥ 70% |
| `app.py`（routes） | 0% | ≥ 60% |
| `web/schema_diff.py` | 0% | ≥ 70% |
