// lib/agent-chat.js — DB Agent 對話共用邏輯
//
// 供 pages/agent.js（完整頁面）與 lib/drawer.js（全站浮動抽屜）共用：
// 送出訊息、以 postSSE 處理 tool_call/tool_result/delta/turn_done 事件序列、
// 把工具呼叫渲染成可折疊步驟（<details>）、串流文字增量填入 AI 訊息氣泡。
// 只依賴呼叫端傳入的 DOM 容器與回呼，不假設頁面結構，讓兩處呼叫端可各自
// 決定版面（完整頁另有側欄工具軌跡，抽屜只有單一訊息串）。

import { ENDPOINTS } from "./api.js";
import { postSSE } from "./sse.js";
import { showToast } from "./toast.js";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function appendUserBubble(container, text) {
  const row = el("div", "chat-bubble-row chat-bubble-row-user");
  row.appendChild(el("div", "chat-bubble chat-bubble-user", text));
  container.appendChild(row);
  container.scrollTop = container.scrollHeight;
}

/** 附上一個可折疊的工具呼叫步驟（<details>），回傳可更新結果的元素。 */
function appendToolStepBubble(container, tool, args) {
  const row = el("div", "chat-bubble-row");
  const details = document.createElement("details");
  details.className = "chat-bubble chat-bubble-tool-step";
  const summary = el("summary", null, `🔧 ${tool} — 執行中…`);
  const argsPre = el("pre", null, JSON.stringify(args, null, 2));
  details.appendChild(summary);
  details.appendChild(argsPre);
  row.appendChild(details);
  container.appendChild(row);
  container.scrollTop = container.scrollHeight;
  return summary;
}

function appendAiBubble(container) {
  const row = el("div", "chat-bubble-row");
  const bubble = el("div", "chat-bubble chat-bubble-ai chat-bubble-loading", "思考中…");
  row.appendChild(bubble);
  container.appendChild(row);
  container.scrollTop = container.scrollHeight;
  return bubble;
}

/**
 * @param {Object} opts
 * @param {HTMLElement} opts.messagesEl 訊息串容器（附加使用者/AI/工具步驟氣泡）
 * @param {() => string|null} [opts.getDbName] 回傳目前選定的業務資料庫名稱
 * @param {(data: object) => void} [opts.onToolCall] tool_call 事件（額外回呼，例如更新側欄軌跡）
 * @param {(data: object) => void} [opts.onToolResult] tool_result 事件
 * @param {(data: object) => void} [opts.onTurnDone] turn_done 事件（{reply, steps, proposal, design_request}）
 * @param {(locked: boolean) => void} [opts.onLockChange] 送出中鎖定/解鎖輸入框
 */
export function createAgentChat(opts) {
  const { messagesEl, getDbName, onToolCall, onToolResult, onTurnDone, onLockChange } = opts;

  function send(message) {
    if (!message.trim()) return;
    appendUserBubble(messagesEl, message);
    if (onLockChange) onLockChange(true);

    const aiBubble = appendAiBubble(messagesEl);
    let started = false;
    const toolSummaries = new Map();

    postSSE(ENDPOINTS.agentChat(), {
      body: { message, db_name: getDbName ? getDbName() : null },
      onEvent: (name, data) => {
        if (name === "tool_call") {
          const summary = appendToolStepBubble(messagesEl, data.tool, data.args);
          toolSummaries.set(data.tool, summary);
          if (onToolCall) onToolCall(data);
        } else if (name === "tool_result") {
          const summary = toolSummaries.get(data.tool);
          if (summary) summary.textContent = `✅ ${data.tool} — ${data.result_summary}`;
          if (onToolResult) onToolResult(data);
        } else if (name === "delta") {
          if (!started) {
            aiBubble.classList.remove("chat-bubble-loading");
            aiBubble.textContent = "";
            started = true;
          }
          aiBubble.textContent += data.text;
          messagesEl.scrollTop = messagesEl.scrollHeight;
        } else if (name === "turn_done") {
          if (!started) {
            aiBubble.classList.remove("chat-bubble-loading");
            aiBubble.textContent = data.reply || "";
          }
          if (onLockChange) onLockChange(false);
          if (onTurnDone) onTurnDone(data);
        }
      },
      onGiveUp: () => {
        if (onLockChange) onLockChange(false);
        showToast("連線中斷，請重新整理", "error");
      },
    });
  }

  return { send };
}
