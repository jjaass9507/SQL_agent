// pages/chat.js — 對話頁行為（stub，API 就緒後接上 lib/api.js 與 lib/sse.js）
import { ENDPOINTS, api } from "../lib/api.js";
import { connectSSE } from "../lib/sse.js";
import { showToast } from "../lib/toast.js";

const layout = document.querySelector("[data-session-id]");
const sessionId = layout ? layout.dataset.sessionId : null;

document.addEventListener("submit", (event) => {
  const form = event.target.closest('[data-action="send-message"]');
  if (!form) return;
  event.preventDefault();

  const input = form.querySelector('[data-target="message-input"]');
  const message = input.value.trim();
  if (!message) return;

  console.log("[chat] send-message", { sessionId, message, endpoint: ENDPOINTS.sessionMessages(sessionId) });
  // TODO(API): POST ENDPOINTS.sessionMessages(sessionId)，Accept: text/event-stream，
  // 以 connectSSE 訂閱 delta / turn_done 事件並串流填入 #chat-messages
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
    console.log("[chat] go-to-confirm", { sessionId });
  }
});

// TODO(API): 生成進度／確認完成推播，/api/v1/sessions/{id}/events 就緒後啟用。
// 斷線重試 3 次後顯示「請重新整理」（NFR-02），行為已由 lib/sse.js 提供：
// connectSSE(ENDPOINTS.sessionEvents(sessionId), {
//   onEvent: (name, data) => console.log("[chat] sse", name, data),
//   onGiveUp: () => showToast("連線中斷，請重新整理", "error"),
// });

console.log("[chat] page module loaded", { sessionId, api, connectSSE, showToast });
