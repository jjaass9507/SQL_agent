// pages/index.js — 首頁行為（stub，API 就緒後接上 lib/api.js）
import { ENDPOINTS, api } from "../lib/api.js";

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;

  if (action === "create-session") {
    const mode = target.dataset.mode;
    console.log("[index] create-session", { mode, endpoint: ENDPOINTS.sessions() });
    // TODO(API): await api.post(ENDPOINTS.sessions(), { mode }) → 導向 /chat/{id} 或 /review/{id}
  }

  if (action === "filter-sessions") {
    const status = target.dataset.status;
    console.log("[index] filter-sessions", { status });
    document.querySelectorAll('[data-action="filter-sessions"]').forEach((btn) => {
      btn.classList.toggle("is-active", btn === target);
    });
    // TODO(API): await api.get(ENDPOINTS.sessions()) 依 status 篩選並重繪 #session-list
  }

  if (action === "open-session") {
    console.log("[index] open-session", { target: target.dataset.target });
  }
});

console.log("[index] page module loaded", { sessionsEndpoint: ENDPOINTS.sessions(), api });
