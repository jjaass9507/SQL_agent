// pages/agent.js — DB Agent 頁：POST SSE 工具迴圈對話 + 待審變更請求面板（HITL）
import { ADMIN_TOKEN_STORAGE_KEY, ENDPOINTS, api, adminHeaders } from "../lib/api.js";
import { createAgentChat } from "../lib/agent-chat.js";
import { showToast } from "../lib/toast.js";

const messagesEl = document.querySelector('[data-target="agent-messages"]');
const traceListEl = document.querySelector('[data-target="agent-tool-trace-list"]');
const dbSelect = document.querySelector('[data-target="db-select"]');

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// ── 側欄工具軌跡 ────────────────────────────────────────────────────────

let traceHasSteps = false;

function appendTraceStep(text, state) {
  if (!traceListEl) return null;
  if (!traceHasSteps) {
    traceListEl.textContent = "";
    traceHasSteps = true;
  }
  const step = el("div", `progress-step is-${state}`);
  step.appendChild(el("span", "progress-step-icon", state === "active" ? "◐" : "✓"));
  step.appendChild(el("span", "progress-step-name", text));
  traceListEl.appendChild(step);
  return step;
}

// ── turn_done 的 proposal / design_request 卡片 ─────────────────────────

function appendProposalCard(proposal) {
  const card = el("div", "card agent-turn-card");
  card.appendChild(el("div", "card-title", "結構變更提案"));
  const shortId = String(proposal.proposal_id || "").slice(0, 8);
  card.appendChild(
    el("p", null, `提案編號 #${shortId}，dry-run ${proposal.dry_run_ok ? "通過" : "未通過"}，狀態：${proposal.status || "pending"}。`)
  );
  card.appendChild(el("p", "form-hint", "待管理員於右側「待審變更請求」面板核准後才會執行。"));
  messagesEl.appendChild(card);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  loadChangeRequests();
}

function appendDesignRequestCard(designRequest) {
  const card = el("div", "card agent-turn-card");
  card.appendChild(el("div", "card-title", "新資料表設計需求"));
  card.appendChild(el("p", null, designRequest));
  const btn = el("button", "btn btn-accent btn-sm", "帶著這份需求開始設計 →");
  btn.type = "button";
  btn.dataset.action = "start-design-from-request";
  btn.dataset.request = designRequest;
  card.appendChild(btn);
  messagesEl.appendChild(card);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

// ── 對話（共用 lib/agent-chat.js） ──────────────────────────────────────

let activeTraceStep = null;

const chat = createAgentChat({
  messagesEl,
  getDbName: () => (dbSelect && dbSelect.value ? dbSelect.value : null),
  onLockChange: (locked) => {
    const input = document.querySelector('[data-target="agent-message-input"]');
    const submit = document.querySelector('[data-action="agent-submit-message"]');
    if (input) input.disabled = locked;
    if (submit) submit.disabled = locked;
  },
  onToolCall: (data) => {
    activeTraceStep = appendTraceStep(`${data.tool}`, "active");
  },
  onToolResult: (data) => {
    if (activeTraceStep) {
      activeTraceStep.className = "progress-step is-done";
      const icon = activeTraceStep.querySelector(".progress-step-icon");
      const name = activeTraceStep.querySelector(".progress-step-name");
      if (icon) icon.textContent = "✓";
      if (name) name.textContent = `${data.tool} — ${data.result_summary}`;
      activeTraceStep = null;
    }
  },
  onTurnDone: (data) => {
    if (data.proposal) appendProposalCard(data.proposal);
    if (data.design_request) appendDesignRequestCard(data.design_request);
  },
});

// ── 待審變更請求面板 ────────────────────────────────────────────────────

async function loadChangeRequests() {
  const list = document.querySelector('[data-target="change-request-list"]');
  if (!list) return;
  let records;
  try {
    records = await api.get(`${ENDPOINTS.changeRequests()}?status=pending`, { silent: true });
  } catch {
    list.textContent = "";
    list.appendChild(el("p", "form-hint", "無法載入待審清單。"));
    return;
  }

  list.textContent = "";
  if (!records.length) {
    list.appendChild(el("p", "form-hint", "目前沒有待審的變更請求。"));
    return;
  }
  for (const record of records) {
    const item = el("div", "change-request-item");
    item.appendChild(el("div", "form-hint", `#${record.id.slice(0, 8)}・${record.db_name || "預設 DB"}・${new Date(record.created_at).toLocaleString()}`));
    item.appendChild(el("pre", "code-block", record.ddl));
    if (record.reason) item.appendChild(el("p", "form-hint", `理由：${record.reason}`));
    const actions = el("div", "modal-actions");
    const approve = el("button", "btn btn-primary btn-sm", "核准並執行");
    approve.type = "button";
    approve.dataset.action = "approve-change-request";
    approve.dataset.target = record.id;
    const reject = el("button", "btn btn-danger btn-sm", "駁回");
    reject.type = "button";
    reject.dataset.action = "reject-change-request";
    reject.dataset.target = record.id;
    actions.appendChild(approve);
    actions.appendChild(reject);
    item.appendChild(actions);
    list.appendChild(item);
  }
}

// ── 資料庫下拉選單（讀設定頁維護的業務 DB 清單） ────────────────────────

async function loadDatabases() {
  if (!dbSelect) return;
  try {
    const settings = await api.get(ENDPOINTS.settings(), { silent: true });
    for (const entry of settings.business_databases || []) {
      const option = document.createElement("option");
      option.value = entry.name;
      option.textContent = entry.name;
      dbSelect.appendChild(option);
    }
  } catch {
    // 設定載入失敗時仍可對話（不指定 db_name）
  }
}

// ── 事件委派 ────────────────────────────────────────────────────────────

document.addEventListener("submit", (event) => {
  const form = event.target.closest('[data-action="agent-send-message"]');
  if (!form) return;
  event.preventDefault();

  const input = form.querySelector('[data-target="agent-message-input"]');
  const message = input.value.trim();
  if (!message || input.disabled) return;
  chat.send(message);
  input.value = "";
});

document.addEventListener("keydown", (event) => {
  if (
    event.target.matches('[data-target="agent-message-input"]') &&
    event.key === "Enter" &&
    !event.shiftKey
  ) {
    event.preventDefault();
    event.target.closest("form").requestSubmit();
  }
});

document.addEventListener("click", async (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;

  if (action === "approve-change-request" || action === "reject-change-request") {
    const decision = action === "approve-change-request" ? "approve" : "reject";
    const endpoint =
      decision === "approve"
        ? ENDPOINTS.changeRequestApprove(target.dataset.target)
        : ENDPOINTS.changeRequestReject(target.dataset.target);
    target.disabled = true;
    try {
      const result = await api.post(endpoint, undefined, { headers: adminHeaders() });
      showToast(
        decision === "approve" ? `已核准，執行結果：${result.status}` : "已駁回",
        result.status === "failed" ? "warning" : "success"
      );
      loadChangeRequests();
    } catch {
      target.disabled = false;
    }
  }

  if (action === "save-admin-token") {
    const input = document.querySelector('[data-target="agent-admin-token"]');
    if (input && input.value.trim()) {
      sessionStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, input.value.trim());
      showToast("Admin Token 已存於此瀏覽器分頁", "success");
    }
  }

  if (action === "start-design-from-request") {
    try {
      const session = await api.post(ENDPOINTS.sessions(), {
        mode: "design",
        title: "來自 DB Agent 的設計需求",
      });
      sessionStorage.setItem(`sqlAgent.designRequest.${session.id}`, target.dataset.request);
      window.location.href = `/chat/${session.id}`;
    } catch {
      // apiFetch 已 toast
    }
  }
});

// 還原已存的 admin token 到輸入框（僅示意已設定，不顯示明文）
const tokenInput = document.querySelector('[data-target="agent-admin-token"]');
if (tokenInput && sessionStorage.getItem(ADMIN_TOKEN_STORAGE_KEY)) {
  tokenInput.placeholder = "已設定（重新輸入可覆蓋）";
}

loadDatabases();
loadChangeRequests();
