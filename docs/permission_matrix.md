# SQL Agent — 權限矩陣

> **目前狀態**：v0.5 尚未實作使用者認證機制，一般端點為公開存取。  
> **例外（已實作）**：結構變更審批（`POST /api/change-requests/<id>/approve|reject`）已加上
> `ADMIN_TOKEN` 共享密鑰防護——見下方「已實作：變更審批 ADMIN_TOKEN」一節。  
> 本文件其餘部分定義 v1.0 目標的角色與權限設計，作為未來認證層實作依據。

---

## 已實作：變更審批 `ADMIN_TOKEN`（Phase 4）

`POST /api/change-requests/<id>/approve` 與 `POST /api/change-requests/<id>/reject`（`web/routes/changes.py`
的 `require_admin` decorator）是目前唯一有存取控制的端點：

| 情境 | 回應 |
|---|---|
| 伺服器未設定環境變數 `ADMIN_TOKEN` | `403`，訊息提示需先設定 |
| 已設定 `ADMIN_TOKEN`，但請求 header `X-Admin-Token` 缺漏或不符 | `401` |
| `X-Admin-Token` 與 `ADMIN_TOKEN` 相符 | 放行 |

`GET /api/change-requests`（列表）與 `POST /api/change-requests`（提案/送審）**不**受此保護——任何人都能
提案一項結構變更，但只有持有權杖者能核准使其真正執行到資料庫上。這是完整 RBAC 落地前的過渡機制：只鎖住
「執行變更」這一個高風險動作，不是通用的使用者權限系統，因此仍列在下方「目前缺口」中。

---

## 角色定義

| 角色 | 說明 | 取得方式 |
|---|---|---|
| **Guest** | 未登入使用者 | 匿名存取 |
| **User** | 已登入使用者 | 帳號密碼登入（v1.0） |
| **Admin** | 系統管理員 | 後台設定指定 |

> v0.5 目前等同於所有使用者具備 User 權限，無隔離。

---

## API 端點權限矩陣

| 端點 | Guest | User（自己的 session） | User（他人的 session） | Admin |
|---|:---:|:---:|:---:|:---:|
| `POST /api/sessions` | ❌ | ✅ | — | ✅ |
| `GET /api/sessions` | ❌ | ✅（只看自己）| ❌ | ✅（全部）|
| `GET /api/sessions/<id>` | ❌ | ✅ | ❌ | ✅ |
| `POST /api/sessions/<id>/messages` | ❌ | ✅ | ❌ | ✅ |
| `POST /api/sessions/<id>/confirm` | ❌ | ✅ | ❌ | ✅ |
| `GET /api/sessions/<id>/outputs` | ❌ | ✅ | ❌ | ✅ |
| `GET /api/sessions/<id>/outputs/zip` | ❌ | ✅ | ❌ | ✅ |
| `POST /api/sessions/<id>/import-db` | ❌ | ✅ | ❌ | ✅ |
| `GET /api/sessions/<id>/versions` | ❌ | ✅ | ❌ | ✅ |
| `POST /api/sessions/<id>/versions/<n>/restore` | ❌ | ✅ | ❌ | ✅ |
| `GET /health` | ✅ | ✅ | ✅ | ✅ |
| `GET /api/admin/sessions` | ❌ | ❌ | ❌ | ✅ |
| `GET /api/admin/logs` | ❌ | ❌ | ❌ | ✅ |
| `GET /api/change-requests` | ✅ | ✅ | ✅ | ✅ |
| `POST /api/change-requests` | ✅ | ✅ | ✅ | ✅ |
| `POST /api/change-requests/<id>/approve` | ❌（已實作 `ADMIN_TOKEN`）| ❌ | ❌ | ✅（`X-Admin-Token`）|
| `POST /api/change-requests/<id>/reject` | ❌（已實作 `ADMIN_TOKEN`）| ❌ | ❌ | ✅（`X-Admin-Token`）|

---

## 頁面路由權限

| 路由 | Guest | User（自己）| User（他人）| Admin |
|---|:---:|:---:|:---:|:---:|
| `GET /` | ❌ redirect login | ✅ | ✅ | ✅ |
| `GET /sessions/<id>/chat` | ❌ | ✅ | ❌ 403 | ✅ |
| `GET /sessions/<id>/confirm` | ❌ | ✅ | ❌ 403 | ✅ |
| `GET /sessions/<id>/docs` | ❌ | ✅ | ❌ 403 | ✅ |
| `GET /sessions/<id>/review` | ❌ | ✅ | ❌ 403 | ✅ |

---

## 資料隔離規則

1. **Session 所有權**：session 建立時記錄 `user_id`，所有後續操作驗證 `session.user_id == current_user.id`
2. **Admin bypass**：Admin 角色可存取任意 session，用於支援與除錯
3. **DB 連線字串**：儲存時加密，只在後端使用，不回傳至前端
4. **Session 刪除**：v0.5 提供硬刪除（`DELETE /api/sessions/<id>` + 首頁兩次點擊確認）。刪除後無法復原，未來版本可考慮軟刪除或回收站機制。

---

## 目前缺口（v0.5 → v1.0 必須補足）

| 缺口 | 影響 | 優先級 |
|---|---|---|
| 無身份驗證 | 任何人可存取/竄改任意 session | 🔴 Critical |
| 無 user_id 記錄 | 無法實作資料隔離 | 🔴 Critical |
| DB 連線字串明文儲存 | 洩漏風險 | 🔴 Critical |
| 無 session 所有權驗證 | UUID 猜測攻擊 | 🟠 High |
| 無 rate limiting | API 濫用風險 | 🟡 Medium |
| 無 Admin 管理頁面 | 無法維護使用者 | 🟡 Medium |
