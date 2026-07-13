// pages/agent.js — DB Agent 頁行為（stub，API 就緒後接上 lib/api.js 與 lib/sse.js）
import { ENDPOINTS, api } from "../lib/api.js";
import { connectSSE } from "../lib/sse.js";
import { showToast } from "../lib/toast.js";

document.addEventListener("submit", (event) => {
  const form = event.target.closest('[data-action="agent-send-message"]');
  if (!form) return;
  event.preventDefault();

  const input = form.querySelector('[data-target="agent-message-input"]');
  const message = input.value.trim();
  if (!message) return;

  console.log("[agent] send-message", { message, endpoint: ENDPOINTS.agentChat() });
  // TODO(API): POST ENDPOINTS.agentChat()，Accept: text/event-stream，
  // 以 connectSSE 訂閱 tool_call / tool_result / delta / turn_done 事件，
  // 即時把工具步驟畫進 #agent-tool-trace-list、文字增量畫進 #agent-messages
  input.value = "";
});

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;

  if (action === "select-db") {
    console.log("[agent] select-db", { value: target.value });
  }

  if (action === "agent-run-ddl") {
    console.log("[agent] agent-run-ddl");
    // TODO(API): 走 change-requests 提案/核准流程，見 v2_rebuild_plan.md 第七章 HITL
  }

  if (action === "agent-dismiss-ddl") {
    const panel = document.getElementById("agent-ddl-confirm");
    if (panel) panel.hidden = true;
  }
});

console.log("[agent] page module loaded", { api, connectSSE, showToast });
