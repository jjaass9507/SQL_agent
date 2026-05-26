# 系統架構說明

## 整體架構

```
使用者 (Terminal CLI)
        │
        ▼
┌─────────────────────────────────────────────────┐
│               Orchestrator                       │
│  狀態機：COLLECTING → CONFIRMING → GENERATING   │
│  協調 Interviewer 與四個 Writer                  │
└───────────────┬─────────────────────────────────┘
                │
     ┌──────────┴──────────┐
     │ 需求收集階段         │ 文件產生階段
     ▼                     ▼
┌──────────────┐  ┌────────────────────┐
│ Interviewer  │  │ SpecWriter         │ → 01_specification.md
│              │  ├────────────────────┤
│ 追問欄位細節  │  │ DiagramWriter      │ → 02_er_diagram.md
│ XML tag 提取 │  ├────────────────────┤
│ TableSpec    │  │ DDLWriter          │ → 03_ddl.sql
└──────────────┘  ├────────────────────┤
                  │ SecurityWriter     │ → 04_security_plan.md
                  └────────────────────┘
                           │
                           ▼
                  output/{timestamp}/
```

---

## 資料流

所有模組共用同一個資料模型，資料只往下流，不回流：

```
使用者文字輸入
    → Interviewer.chat()
        → PensieveAPI.chat(question, answer)
        → 解析 <TABLE_SPECS> XML tag
    → list[TableSpec]
        → SpecWriter.generate(tables)   → str (Markdown)
        → DiagramWriter.generate(tables) → str (Markdown)
        → DDLWriter.generate(tables)     → str (SQL)
        → SecurityWriter.generate(tables)→ str (Markdown)
    → file_writer.write_outputs()
        → output/{timestamp}/*.md /*.sql
```

---

## Agent 角色

### Orchestrator（`agents/orchestrator.py`）

狀態機管理者，不直接呼叫 API。

```
Phase.COLLECTING
  ↓ 收到使用者輸入 → 轉給 Interviewer
  ↓ Interviewer 回傳 TableSpec → 切換至 CONFIRMING

Phase.CONFIRMING
  ↓ 使用者輸入確認詞 → 切換至 GENERATING
  ↓ 使用者輸入其他 → 回到 COLLECTING（繼續修改需求）

Phase.GENERATING
  ↓ 依序呼叫四個 Writer → 寫出檔案
  ↓ 切換至 DONE
```

確認詞集合：`ok`, `確認`, `yes`, `confirm`, `好`, `可以`, `沒問題`

---

### Interviewer（`agents/interviewer.py`）

- 維護本地 `_history`（`list[dict]`），記錄每輪對話
- 每次 API 呼叫傳入：
  - `question` = system prompt + 所有歷史對話
  - `answer` = 本輪使用者輸入
- 當需求完整時，LLM 在回覆末尾附加 `<TABLE_SPECS>...</TABLE_SPECS>` JSON
- Regex 解析後轉換為 `list[TableSpec]`，回傳給 Orchestrator

---

### Writers（`agents/writers/`）

統一介面：`generate(tables: list[TableSpec]) -> str`

| Writer | API 呼叫 | 說明 |
|---|---|---|
| SpecWriter | **無** | 直接模板渲染，固定格式 Markdown 表格 |
| DiagramWriter | 1 次 | `question`=任務說明，`answer`=TableSpec JSON |
| DDLWriter | 1 次 | `question`=任務說明，`answer`=TableSpec JSON |
| SecurityWriter | 1 次 | `question`=任務說明（含敏感欄位偵測結果），`answer`=TableSpec JSON |

SpecWriter 不呼叫 API 的原因：規格書格式完全確定，從結構化資料直接渲染比 LLM 更快、更穩定、節省費用。

---

## 核心資料模型（`models/schema.py`）

```python
@dataclass
class ColumnSpec:
    name: str           # 欄位名稱
    data_type: str      # PostgreSQL 型態（UUID, VARCHAR, TIMESTAMPTZ...）
    nullable: bool
    description: str
    is_primary_key: bool = False
    is_foreign_key: bool = False
    references: str | None = None   # "other_table.column"
    is_unique: bool = False
    is_indexed: bool = False
    length: int | None = None
    default: str | None = None

@dataclass
class TableSpec:
    table_name: str
    description: str
    columns: list[ColumnSpec]
    constraints: list[str] = []     # CHECK constraints
    related_tables: list[str] = []
```

---

## API 整合（`utils/client.py`）

使用 PensieveAPI 模式，統一的 HTTP 呼叫封裝：

```python
payload = {
    "token": ...,
    "empno": ...,
    "variables": {
        "building": ...,   # flow 名稱，來自 PENSIEVE_BUILDING 環境變數
        "question": ...,   # 任務描述 / system prompt + 上下文
        "answer": ...,     # 當前輸入 / 結構化資料
    }
}
# POST → 解析 response["Result"] → 回傳純文字
```

`get_api()` 提供 singleton，避免重複讀取環境變數。

---

## 輸出格式說明

### `01_specification.md` — 規格書與資料字典

每個資料表產出一個 Markdown 表格：

```markdown
## 資料表：`orders`
**說明**：訂單主表

| 欄位名稱 | 資料型態 | 長度 | 允許 NULL | 預設值 | PK | FK | UNIQUE | INDEX | 說明 |
|----------|----------|------|-----------|--------|----|----|--------|-------|------|
| `id`     | UUID     |      | 否        |        | ✓  |    |        |       | 訂單 ID |
```

### `02_er_diagram.md` — ER Diagram

包含設計說明文字 + Mermaid 程式碼區塊，可直接貼到 [mermaid.live](https://mermaid.live) 預覽：

````markdown
```mermaid
erDiagram
    orders {
        UUID id PK
        UUID user_id FK
        ...
    }
    orders ||--o{ order_items : "包含"
```
````

### `03_ddl.sql` — DDL 腳本

依序包含四個區塊：

```sql
-- === 建立腳本 ===
CREATE TABLE orders ( ... );
COMMENT ON COLUMN orders.id IS '訂單唯一識別碼';

-- === 索引建立 ===
CREATE INDEX idx_orders_user_id ON orders(user_id);

-- === Migration 腳本 ===
CREATE TABLE IF NOT EXISTS orders ( ... );
-- 回滾：DROP TABLE IF EXISTS orders;

-- === Seed Data ===
INSERT INTO orders VALUES (...);
```

### `04_security_plan.md` — 效能與安全規劃書

六個章節：索引策略、查詢效能建議、分區策略、存取控制（含 GRANT 範例）、敏感欄位安全、備份與維運建議。
