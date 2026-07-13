// pages/review.js — 審查報告頁行為（stub，API 就緒後接上 lib/api.js）
import { ENDPOINTS, api } from "../lib/api.js";

const layout = document.querySelector("[data-session-id]");
const sessionId = layout ? layout.dataset.sessionId : null;

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;

  if (target.dataset.action === "download-report") {
    console.log("[review] download-report", { sessionId, endpoint: ENDPOINTS.sessionOutputs(sessionId) });
    // TODO(API): 下載審查報告 Markdown
  }
});

console.log("[review] page module loaded", { sessionId, api });
