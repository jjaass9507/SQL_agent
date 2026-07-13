// lib/api.js — fetch 封裝 + 端點常數
//
// 端點對齊 docs/v2_rebuild_plan.md 第六章「API 設計」，全部掛 /api/v1/ 前綴。
// TODO(API): 後端尚未實作，以下呼叫在對應 endpoint 上線前會回 404/501。

const API_PREFIX = "/api/v1";

export const ENDPOINTS = {
  sessions: () => `${API_PREFIX}/sessions`,
  session: (id) => `${API_PREFIX}/sessions/${id}`,
  sessionMessages: (id) => `${API_PREFIX}/sessions/${id}/messages`,
  sessionEvents: (id) => `${API_PREFIX}/sessions/${id}/events`,
  sessionConfirm: (id) => `${API_PREFIX}/sessions/${id}/confirm`,
  sessionVersions: (id) => `${API_PREFIX}/sessions/${id}/versions`,
  sessionVersionRestore: (id, version) => `${API_PREFIX}/sessions/${id}/versions/${version}/restore`,
  sessionOutputs: (id) => `${API_PREFIX}/sessions/${id}/outputs`,
  sessionOutputsZip: (id) => `${API_PREFIX}/sessions/${id}/outputs/zip`,
  sessionExtras: (id) => `${API_PREFIX}/sessions/${id}/extras`,
  sessionDdlImport: () => `${API_PREFIX}/sessions/ddl-import`,
  agentChat: () => `${API_PREFIX}/agent/chat`,
  llmHealth: () => `${API_PREFIX}/llm/health`,
  llmDiagnose: () => `${API_PREFIX}/llm/diagnose`,
  workbenchQuery: () => `${API_PREFIX}/workbench/query`,
  workbenchExplain: () => `${API_PREFIX}/workbench/explain`,
  workbenchNl2sql: () => `${API_PREFIX}/workbench/nl2sql`,
  workbenchSchemaTree: () => `${API_PREFIX}/workbench/schema-tree`,
  workbenchValidateDdl: () => `${API_PREFIX}/workbench/validate-ddl`,
  changeRequestApprove: (id) => `${API_PREFIX}/change-requests/${id}/approve`,
  changeRequestReject: (id) => `${API_PREFIX}/change-requests/${id}/reject`,
  settings: () => `${API_PREFIX}/settings`,
  activity: () => `${API_PREFIX}/activity`,
};

/**
 * 統一 fetch 封裝：JSON 進出、非 2xx 拋出含 status 的 Error。
 * @param {string} url
 * @param {RequestInit} [options]
 */
export async function apiFetch(url, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const response = await fetch(url, { ...options, headers });

  if (!response.ok) {
    const error = new Error(`API ${response.status}: ${url}`);
    error.status = response.status;
    throw error;
  }

  if (response.status === 204) return null;
  return response.json();
}

export const api = {
  get: (url) => apiFetch(url, { method: "GET" }),
  post: (url, body) => apiFetch(url, { method: "POST", body: JSON.stringify(body) }),
  put: (url, body) => apiFetch(url, { method: "PUT", body: JSON.stringify(body) }),
  delete: (url) => apiFetch(url, { method: "DELETE" }),
};
