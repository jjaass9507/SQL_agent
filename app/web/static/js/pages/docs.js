// pages/docs.js — 文件查閱頁行為（stub，API 就緒後接上 lib/api.js 與 lib/sse.js）
import { ENDPOINTS, api } from "../lib/api.js";
import { connectSSE } from "../lib/sse.js";
import { showToast } from "../lib/toast.js";

const layout = document.querySelector("[data-session-id]");
const sessionId = layout ? layout.dataset.sessionId : null;

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;

  if (action === "switch-tab") {
    const tabName = target.dataset.target;
    document.querySelectorAll('[data-action="switch-tab"]').forEach((tab) => {
      tab.classList.toggle("is-active", tab === target);
    });
    document.querySelectorAll('[data-target="doc-panel"]').forEach((panel) => {
      panel.classList.toggle("is-active", panel.dataset.panel === tabName);
    });
  }

  if (action === "copy-code") {
    const codeEl = document.querySelector(`[data-target="doc-content-${target.dataset.target}"]`);
    if (codeEl && navigator.clipboard) {
      navigator.clipboard.writeText(codeEl.textContent);
    }
    console.log("[docs] copy-code", { target: target.dataset.target });
  }

  if (action === "download-single") {
    console.log("[docs] download-single", { sessionId, endpoint: ENDPOINTS.sessionOutputs(sessionId) });
    // TODO(API): 依目前分頁下載單一文件
  }

  if (action === "download-all") {
    console.log("[docs] download-all", { sessionId, endpoint: ENDPOINTS.sessionOutputsZip(sessionId) });
    // TODO(API): window.location = ENDPOINTS.sessionOutputsZip(sessionId)
  }
});

// TODO(API): 產出進度推播，/api/v1/sessions/{id}/events 就緒後啟用：
// connectSSE(ENDPOINTS.sessionEvents(sessionId), {
//   onEvent: (name, data) => console.log("[docs] sse", name, data),
//   onGiveUp: () => showToast("連線中斷，請重新整理", "error"),
// });

console.log("[docs] page module loaded", { sessionId, api, connectSSE, showToast });
