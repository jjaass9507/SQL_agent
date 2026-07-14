// pages/settings.js — 設定頁：業務 DB 增刪（遮罩顯示）、LLM health/diagnose、activity 列表
import { ADMIN_TOKEN_STORAGE_KEY, ENDPOINTS, api } from "../lib/api.js";
import { showToast } from "../lib/toast.js";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// ── 記憶後端狀態 + 業務資料庫清單 ───────────────────────────────────────

function renderBackend(settings) {
  const backendEl = document.querySelector('[data-target="memory-backend"]');
  if (backendEl) {
    backendEl.textContent = `${settings.backend}（${settings.masked_url}）`;
  }
}

function renderBusinessDbs(entries) {
  const list = document.querySelector('[data-target="business-db-list"]');
  if (!list) return;
  list.textContent = "";
  if (!entries.length) {
    list.appendChild(el("p", "form-hint", "尚未設定任何業務資料庫。"));
    return;
  }
  for (const entry of entries) {
    const row = el("div", "settings-db-row");
    const meta = el("div", "page-index-session-meta");
    meta.appendChild(el("strong", null, entry.name));
    meta.appendChild(el("span", "form-hint", entry.masked_url));
    row.appendChild(meta);
    const remove = el("button", "btn btn-danger btn-sm", "刪除");
    remove.type = "button";
    remove.dataset.action = "remove-business-db";
    remove.dataset.target = entry.name;
    row.appendChild(remove);
    list.appendChild(row);
  }
}

async function loadSettings() {
  try {
    const settings = await api.get(ENDPOINTS.settings());
    renderBackend(settings);
    renderBusinessDbs(settings.business_databases || []);
  } catch {
    // apiFetch 已 toast
  }
}

// ── LLM health / diagnose ───────────────────────────────────────────────

function renderProfile(profile) {
  const keys = ["multi_turn", "system_role", "native_tools", "json_schema", "streaming"];
  for (const key of keys) {
    const pill = document.querySelector(`[data-target="capability-${key}"]`);
    if (!pill) continue;
    const supported = profile[key];
    pill.className = `status-pill status-pill-${supported ? "done" : "failed"}`;
    pill.textContent = `${key}: ${supported ? "支援" : "不支援"}`;
  }
  const probedAt = document.querySelector('[data-target="capability-probed-at"]');
  if (probedAt) {
    probedAt.textContent = profile.probed_at
      ? `上次探測：${new Date(profile.probed_at).toLocaleString()}`
      : "尚未探測過（顯示為預設值），點「重新探測」執行。";
  }
}

async function testConnection(button) {
  const resultEl = document.querySelector('[data-target="llm-health-result"]');
  button.disabled = true;
  if (resultEl) resultEl.textContent = "測試中…";
  try {
    const health = await api.get(ENDPOINTS.llmHealth());
    if (resultEl) {
      resultEl.textContent = health.ok
        ? `✓ 連線正常（model: ${health.model || "未知"}）`
        : "✗ 連線失敗，請檢查 LLM_BASE_URL / LLM_API_KEY 環境變數";
    }
    renderProfile(health.profile);
  } catch {
    if (resultEl) resultEl.textContent = "✗ 測試失敗";
  } finally {
    button.disabled = false;
  }
}

async function diagnose(button) {
  button.disabled = true;
  button.textContent = "探測中…";
  try {
    const result = await api.post(ENDPOINTS.llmDiagnose());
    renderProfile(result.profile);
    showToast("能力探測完成", "success");
  } catch {
    // apiFetch 已 toast
  } finally {
    button.disabled = false;
    button.textContent = "重新探測";
  }
}

// ── 稽核紀錄 ────────────────────────────────────────────────────────────

async function loadActivity() {
  const list = document.querySelector('[data-target="activity-list"]');
  if (!list) return;
  let records;
  try {
    records = await api.get(`${ENDPOINTS.activity()}?limit=50`, { silent: true });
  } catch {
    list.textContent = "";
    list.appendChild(el("p", "form-hint", "無法載入稽核紀錄。"));
    return;
  }
  list.textContent = "";
  if (!records.length) {
    list.appendChild(el("p", "form-hint", "尚無稽核紀錄。"));
    return;
  }
  for (const record of records) {
    const row = el("div", "settings-activity-row");
    row.appendChild(el("span", "code-inline", record.event));
    const detailText = record.detail ? JSON.stringify(record.detail) : "";
    row.appendChild(el("span", "form-hint", `${new Date(record.created_at).toLocaleString()} ${detailText}`));
    list.appendChild(row);
  }
}

// ── 事件委派 ────────────────────────────────────────────────────────────

document.addEventListener("submit", async (event) => {
  const form = event.target;
  if (!form.matches('[data-action="add-business-db"]')) return;
  event.preventDefault();

  const name = form.querySelector('[data-target="business-db-name"]').value.trim();
  const url = form.querySelector('[data-target="business-db-url"]').value.trim();
  if (!name || !url) {
    showToast("請填入名稱與連線字串", "warning");
    return;
  }
  try {
    const result = await api.post(ENDPOINTS.settingsBusinessDb(), { name, url });
    renderBusinessDbs(result.business_databases);
    showToast("已新增業務資料庫", "success");
    form.reset();
    loadActivity();
  } catch {
    // apiFetch 已 toast（含後端連線測試失敗訊息）
  }
});

document.addEventListener("click", async (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;

  if (action === "test-connection") testConnection(target);
  if (action === "diagnose-llm") diagnose(target);

  if (action === "remove-business-db") {
    target.disabled = true;
    try {
      const result = await api.delete(
        `${ENDPOINTS.settingsBusinessDb()}?name=${encodeURIComponent(target.dataset.target)}`
      );
      renderBusinessDbs(result.business_databases);
      showToast("已刪除", "success");
      loadActivity();
    } catch {
      target.disabled = false;
    }
  }

  if (action === "save-admin-token") {
    const input = document.querySelector('[data-target="admin-token"]');
    if (input && input.value.trim()) {
      sessionStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, input.value.trim());
      input.value = "";
      input.placeholder = "已設定（重新輸入可覆蓋）";
      showToast("Admin Token 已存於此瀏覽器分頁", "success");
    }
  }
});

// 已存 token 時提示（不顯示明文）
const tokenInput = document.querySelector('[data-target="admin-token"]');
if (tokenInput && sessionStorage.getItem(ADMIN_TOKEN_STORAGE_KEY)) {
  tokenInput.placeholder = "已設定（重新輸入可覆蓋）";
}

loadSettings();
loadActivity();
