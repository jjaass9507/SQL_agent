// lib/api.js — fetch 封裝 + 端點常數
//
// 端點對齊實際後端路由（app/api/routers/*.py），全部掛 /api/v1/ 前綴。
// 非 2xx 一律拋出 Error 並統一以 toast 顯示（見 apiFetch）。

import { showToast } from "./toast.js";

const API_PREFIX = "/api/v1";

export const ENDPOINTS = {
  sessions: () => `${API_PREFIX}/sessions`,
  session: (id) => `${API_PREFIX}/sessions/${id}`,
  sessionMessages: (id) => `${API_PREFIX}/sessions/${id}/messages`,
  sessionEvents: (id, jobId) =>
    `${API_PREFIX}/sessions/${id}/events${jobId ? `?job_id=${jobId}` : ""}`,
  sessionConfirm: (id) => `${API_PREFIX}/sessions/${id}/confirm`,
  sessionVersions: (id) => `${API_PREFIX}/sessions/${id}/versions`,
  sessionVersionRestore: (id, version) => `${API_PREFIX}/sessions/${id}/versions/${version}/restore`,
  sessionImportDb: (id) => `${API_PREFIX}/sessions/${id}/import-db`,
  sessionOutputs: (id) => `${API_PREFIX}/sessions/${id}/outputs`,
  sessionOutputsZip: (id) => `${API_PREFIX}/sessions/${id}/outputs/zip`,
  sessionExtraGenerate: (id, kind) => `${API_PREFIX}/sessions/${id}/extras/${kind}/generate`,
  ddlImport: () => `${API_PREFIX}/ddl-import`,
  agentChat: () => `${API_PREFIX}/agent/chat`,
  llmHealth: () => `${API_PREFIX}/llm/health`,
  llmDiagnose: () => `${API_PREFIX}/llm/diagnose`,
  workbenchQuery: (sessionId) => `${API_PREFIX}/sessions/${sessionId}/query`,
  workbenchExplain: (sessionId) => `${API_PREFIX}/sessions/${sessionId}/explain`,
  workbenchNl2sql: (sessionId) => `${API_PREFIX}/sessions/${sessionId}/nl2sql`,
  workbenchSchemaTree: (sessionId) => `${API_PREFIX}/sessions/${sessionId}/schema-tree`,
  workbenchValidateDdl: (sessionId) => `${API_PREFIX}/sessions/${sessionId}/validate-ddl`,
  changeRequests: () => `${API_PREFIX}/change-requests`,
  changeRequestApprove: (id) => `${API_PREFIX}/change-requests/${id}/approve`,
  changeRequestReject: (id) => `${API_PREFIX}/change-requests/${id}/reject`,
  settings: () => `${API_PREFIX}/settings`,
  settingsBusinessDb: () => `${API_PREFIX}/settings/business-db`,
  activity: () => `${API_PREFIX}/activity`,
  authLogin: () => `${API_PREFIX}/auth/login`,
  authLogout: () => `${API_PREFIX}/auth/logout`,
  authMe: () => `${API_PREFIX}/auth/me`,
  authSso: () => `${API_PREFIX}/auth/sso`,
};

/** /auth/* 呼叫不觸發 401 自動導向登入頁（登入頁本身就是呼叫這些端點的地方）。 */
const AUTH_PATH_PREFIX = `${API_PREFIX}/auth/`;

/** sessionStorage 存放 X-Admin-Token 的 key（HITL 過渡機制，見 docs/v2_rebuild_plan.md 第七章）。 */
export const ADMIN_TOKEN_STORAGE_KEY = "sqlAgent.adminToken";

/**
 * 統一 fetch 封裝：JSON 進出、非 2xx 拋出含 status 的 Error 並顯示 toast。
 * @param {string} url
 * @param {RequestInit} [options]
 * @param {{silent?: boolean, headers?: object}} [opts]
 *   silent 時不彈 toast（呼叫端自行處理錯誤訊息）；headers 附加額外標頭（如 X-Admin-Token）
 */
export async function apiFetch(url, options = {}, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  let response;
  try {
    response = await fetch(url, { ...options, headers });
  } catch (err) {
    if (!opts.silent) showToast("網路連線失敗，請確認連線後重試", "error");
    throw err;
  }

  if (!response.ok) {
    // AUTH_ENABLED=false 時後端不會回 401，此分支對匿名模式零影響。
    if (response.status === 401 && !url.startsWith(AUTH_PATH_PREFIX)) {
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      window.location.href = `/login?next=${next}`;
    }

    let detail = "";
    try {
      const body = await response.clone().json();
      detail = body.detail ? `：${JSON.stringify(body.detail)}` : "";
    } catch {
      // 非 JSON 錯誤內容，略過
    }
    const error = new Error(`API ${response.status}${detail}`);
    error.status = response.status;
    if (!opts.silent) showToast(`操作失敗（${response.status}）${detail}`, "error");
    throw error;
  }

  if (response.status === 204) return null;
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

/** 附上 X-Admin-Token header（若 sessionStorage 有存），用於 change-requests 審批。 */
export function adminHeaders() {
  const token = sessionStorage.getItem(ADMIN_TOKEN_STORAGE_KEY);
  return token ? { "X-Admin-Token": token } : {};
}

export const api = {
  get: (url, opts = {}) => apiFetch(url, { method: "GET", headers: opts.headers }, opts),
  post: (url, body, opts = {}) =>
    apiFetch(
      url,
      {
        method: "POST",
        body: body !== undefined ? JSON.stringify(body) : undefined,
        headers: opts.headers,
      },
      opts
    ),
  put: (url, body, opts = {}) =>
    apiFetch(url, { method: "PUT", body: JSON.stringify(body), headers: opts.headers }, opts),
  delete: (url, opts = {}) => apiFetch(url, { method: "DELETE", headers: opts.headers }, opts),
};
