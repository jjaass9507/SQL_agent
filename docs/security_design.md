# SQL Agent — 安全性設計文件

> **現況（v0.5）**：尚未實作認證機制，所有端點公開存取。  
> 本文件定義 **v1.0 安全性目標**，作為實作依據。

---

## 一、威脅模型

| 威脅 | 當前風險 | 緩解措施（v1.0） |
|---|---|---|
| 未授權讀取任意 session | 🔴 高（UUID 可猜測攻擊） | JWT 認證 + session 所有權驗證 |
| DB 連線字串洩漏 | 🔴 高（明文存入 JSON） | 加密儲存、不回傳前端、不寫入 log |
| LLM Prompt Injection | 🟠 中（使用者輸入直接進入 prompt） | 輸入長度限制、輸出格式驗證 |
| API 濫用 / DoS | 🟠 中（無速率限制） | Rate limiting（per IP 或 per user） |
| XSS（Mermaid 渲染） | 🟡 低（已 HTML escape） | 保持 escape，限制 mermaid 注入 |
| SQL Injection | N/A（目前無 DB） | SQLAlchemy ORM 參數化查詢 |

---

## 二、認證設計（v1.0）

### JWT Bearer Token

```
POST /auth/login
  → { email, password }
  ← { access_token, refresh_token, expires_in }

Authorization: Bearer <access_token>
```

- **Token 類型**：JWT（JSON Web Token），HS256 簽名
- **存活時間**：Access Token 15 分鐘；Refresh Token 7 天
- **儲存位置（前端）**：HttpOnly Cookie（防 XSS 竊取）
- **Refresh 流程**：`POST /auth/refresh` 憑 Refresh Token 換新 Access Token
- **撤銷**：Refresh Token 儲存於 DB（支援登出後作廢）

### Token Payload

```json
{
  "sub": "<user_uuid>",
  "role": "user",
  "iat": 1700000000,
  "exp": 1700000900
}
```

---

## 三、授權設計（v1.0）

### Session 所有權驗證

每個 API 路由（/api/sessions/<id>/*）在回傳資料前驗證：

```python
session = get_session(session_id)
if session["user_id"] != current_user.id and current_user.role != "admin":
    abort(403)
```

### 角色權限

| 操作 | User（自己） | User（他人） | Admin |
|---|:---:|:---:|:---:|
| 讀取 / 操作自己的 session | ✅ | ❌ 403 | ✅ |
| 讀取所有 sessions | ❌ | ❌ | ✅ |
| 刪除任意 session | ❌ | ❌ | ✅ |
| 查閱系統日誌 | ❌ | ❌ | ✅ |

---

## 四、輸入驗證

### 請求驗證層（Pydantic v2）

`web/schemas.py` 定義所有請求 schema；v1.0 接入 app.py 路由：

| 端點 | Schema | 關鍵限制 |
|---|---|---|
| POST /api/sessions | `CreateSessionRequest` | title ≤ 200 字元；mode 枚舉值 |
| POST /messages | `SendMessageRequest` | content 1–10,000 字元 |
| POST /import-db | `ImportDbRequest` | db_url 必填 |

### LLM 輸入防護

- 使用者訊息最大 10,000 字元（Pydantic 驗證）
- AI 回覆在前端顯示前 HTML escape（防 XSS）
- Mermaid diagram source 不傳入 SQL 或 JS 執行環境
- `_parse_tables()` 所有 LLM 輸出欄位均有 `.get(key, default)` 防護

---

## 五、敏感資料處理

### DB 連線字串

| 情境 | 規則 |
|---|---|
| 儲存至 DB | 使用 **AES-256-GCM** 加密，金鑰從環境變數讀取 |
| 回傳前端 | **絕對不回傳**（API 回應中過濾此欄位） |
| 寫入 log | **絕對不記錄**（log filter 遮罩 `db_url` 欄位） |
| 傳輸 | HTTPS only（TLS 1.2+） |

### 密碼欄位偵測

`SecurityWriter` 生成的安全規劃文件中，AI 會標記疑似密碼/token 欄位（含 `password`、`token`、`secret`、`key`、`hash` 的欄位名稱）並建議加密處置。

### 環境設定

```
# .env（不得 commit 至版控）
PENSIEVE_API_KEY=...
SECRET_KEY=...            # Flask session secret / JWT signing key
DB_ENCRYPTION_KEY=...     # 32-byte hex for AES-256
DATABASE_URL=...          # App 自身的 PostgreSQL
```

`.env` 已列入 `.gitignore`，不得 hardcode 任何密鑰於程式碼。

---

## 六、傳輸安全

- **HTTPS（TLS 1.2+）**：Load Balancer 層終止 TLS；app container 只暴露 HTTP（內網）
- **HSTS**：`Strict-Transport-Security: max-age=31536000; includeSubDomains`
- **Cookie 屬性**：`HttpOnly; Secure; SameSite=Lax`
- **CORS**：僅允許白名單 origin（`CORS_ORIGINS` 環境變數設定）

---

## 七、Audit Trail

每個關鍵操作寫入結構化 audit log（JSON 格式，寫入 `audit_logs` 資料表或 stdout）：

```json
{
  "timestamp": "2026-05-28T10:00:00Z",
  "event": "session.confirm",
  "user_id": "<uuid>",
  "session_id": "<uuid>",
  "ip": "1.2.3.4",
  "result": "ok"
}
```

記錄事件：
- `auth.login` / `auth.logout` / `auth.login_failed`
- `session.create` / `session.confirm` / `session.restore_version`
- `session.import_db`（記錄 table count，不記錄 db_url）
- `admin.session_access`（Admin 存取他人 session）

---

## 八、現況缺口清單（v0.5 → v1.0）

| 缺口 | 優先級 | 負責階段 |
|---|---|---|
| 無認證（JWT） | 🔴 Critical | Phase 4 |
| 無 session 所有權驗證 | 🔴 Critical | Phase 4 |
| DB 連線字串明文儲存 | 🔴 Critical | Phase 4 |
| 無輸入驗證層（Pydantic 未接入） | 🟠 High | Phase 3/4 |
| 無速率限制 | 🟡 Medium | Phase 4 |
| 無 HTTPS | 🟡 Medium | Phase 5（部署） |
| 無 Audit Log | 🟡 Medium | Phase 4 |
