// pages/chat.js — 需求收集對話頁：POST SSE 串流對話 + 右側收集進度面板
import { ENDPOINTS, api } from "../lib/api.js";
import { postSSE } from "../lib/sse.js";
import { showToast } from "../lib/toast.js";

const layout = document.querySelector("[data-session-id]");
const sessionId = layout ? layout.dataset.sessionId : null;

const messagesEl = document.querySelector('[data-target="chat-messages"]');
const progressListEl = document.querySelector('[data-target="collection-progress-list"]');
const ctaBtn = document.querySelector('[data-action="go-to-confirm"]');

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function appendUserBubble(text) {
  const row = el("div", "chat-bubble-row chat-bubble-row-user");
  row.appendChild(el("div", "chat-bubble chat-bubble-user", text));
  messagesEl.appendChild(row);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function appendAiBubble() {
  const row = el("div", "chat-bubble-row");
  const bubble = el("div", "chat-bubble chat-bubble-ai chat-bubble-loading", "思考中…");
  row.appendChild(bubble);
  messagesEl.appendChild(row);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return bubble;
}

// AI 思考中鎖定輸入框與送出按鈕
function setInputLocked(locked) {
  const input = document.querySelector('[data-target="message-input"]');
  const submit = document.querySelector('[data-action="submit-message"]');
  if (input) input.disabled = locked;
  if (submit) submit.disabled = locked;
}

// turn_done 後更新右側進度面板：已收集資料表/欄位
function renderProgress(tables) {
  if (!progressListEl) return;
  progressListEl.textContent = "";
  if (!tables || !tables.length) {
    progressListEl.appendChild(
      el("p", "form-hint", "尚未收集到資料表，開始對話後這裡會即時更新。")
    );
    return;
  }
  for (const table of tables) {
    const step = el("div", "progress-step is-done");
    step.appendChild(el("span", "progress-step-icon", "✓"));
    step.appendChild(
      el("span", "progress-step-name", `${table.table_name}（${table.columns.length} 欄位）`)
    );
    progressListEl.appendChild(step);
  }
}

// tables_ready 時顯示「前往確認頁」橫幅 + 啟用 CTA 按鈕
function showTablesReady() {
  if (ctaBtn) ctaBtn.disabled = false;
  if (!document.querySelector('[data-target="tables-ready-banner"]')) {
    const banner = el("div", "card chat-tables-ready-banner");
    banner.dataset.target = "tables-ready-banner";
    banner.appendChild(el("span", null, "✓ 需求已收集完成，可前往確認頁檢視結構化 Schema。"));
    const link = el("a", "btn btn-accent btn-sm", "前往確認 →");
    link.href = `/confirm/${sessionId}`;
    banner.appendChild(link);
    messagesEl.appendChild(banner);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
}

function sendMessage(content) {
  appendUserBubble(content);
  setInputLocked(true);
  const aiBubble = appendAiBubble();
  let started = false;

  postSSE(ENDPOINTS.sessionMessages(sessionId), {
    body: { content },
    onEvent: (name, data) => {
      if (name === "delta") {
        if (!started) {
          aiBubble.classList.remove("chat-bubble-loading");
          aiBubble.textContent = "";
          started = true;
        }
        aiBubble.textContent += data.delta;
        messagesEl.scrollTop = messagesEl.scrollHeight;
      } else if (name === "turn_done") {
        if (!started) {
          aiBubble.classList.remove("chat-bubble-loading");
          aiBubble.textContent = data.reply || "";
        }
        setInputLocked(false);
        if (data.tables) renderProgress(data.tables);
        if (data.tables_ready) showTablesReady();
      }
    },
    onGiveUp: () => {
      setInputLocked(false);
      aiBubble.classList.remove("chat-bubble-loading");
      aiBubble.textContent = "（連線中斷）";
      showToast("連線中斷，請重新整理", "error");
    },
  });
}

document.addEventListener("submit", (event) => {
  const form = event.target.closest('[data-action="send-message"]');
  if (!form) return;
  event.preventDefault();

  const input = form.querySelector('[data-target="message-input"]');
  const message = input.value.trim();
  if (!message || input.disabled) return;

  sendMessage(message);
  input.value = "";
});

document.addEventListener("keydown", (event) => {
  if (event.target.matches('[data-target="message-input"]') && event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    event.target.closest("form").requestSubmit();
  }
});

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;

  if (target.dataset.action === "go-to-confirm") {
    window.location.href = `/confirm/${sessionId}`;
  }
});

// 重新整理後還原收集進度（訊息歷史後端尚無查詢端點，僅還原面板與 CTA 狀態）
async function restoreState() {
  if (!sessionId) return;
  try {
    const detail = await api.get(ENDPOINTS.session(sessionId), { silent: true });
    if (detail.latest_tables) renderProgress(detail.latest_tables);
    if (detail.phase === "confirming") showTablesReady();
  } catch {
    // 還原失敗不阻擋對話（例如 session 剛建立尚無資料）
  }
}

restoreState();

// 從 DB Agent「新資料表設計需求」卡片帶過來的需求文字 → 預填輸入框
const handoffKey = `sqlAgent.designRequest.${sessionId}`;
const handoff = sessionStorage.getItem(handoffKey);
if (handoff) {
  const input = document.querySelector('[data-target="message-input"]');
  if (input) input.value = handoff;
  sessionStorage.removeItem(handoffKey);
}
