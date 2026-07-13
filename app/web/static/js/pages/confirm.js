// pages/confirm.js — 需求確認頁行為（stub，API 就緒後接上 lib/api.js）
import { ENDPOINTS, api } from "../lib/api.js";

const layout = document.querySelector("[data-session-id]");
const sessionId = layout ? layout.dataset.sessionId : null;

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;

  if (action === "confirm-generate") {
    console.log("[confirm] confirm-generate", { sessionId, endpoint: ENDPOINTS.sessionConfirm(sessionId) });
    // TODO(API): await api.post(ENDPOINTS.sessionConfirm(sessionId)) → 導向 /docs/{id}
  }

  if (action === "restore-version") {
    const select = document.querySelector('[data-target="version-select"]');
    const version = select ? select.value : null;
    console.log("[confirm] restore-version", { sessionId, version });
    // TODO(API): await api.post(ENDPOINTS.sessionVersionRestore(sessionId, version))
  }
});

console.log("[confirm] page module loaded", { sessionId, api });
