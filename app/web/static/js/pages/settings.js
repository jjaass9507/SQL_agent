// pages/settings.js — 設定頁行為（stub，API 就緒後接上 lib/api.js）
import { ENDPOINTS, api } from "../lib/api.js";

document.addEventListener("submit", (event) => {
  const form = event.target.closest('[data-action="save-llm-settings"]');
  if (!form) return;
  event.preventDefault();

  const payload = {
    llm_base_url: form.querySelector('[data-target="llm-base-url"]').value,
    llm_model: form.querySelector('[data-target="llm-model"]').value,
  };
  console.log("[settings] save-llm-settings", { payload, endpoint: ENDPOINTS.settings() });
  // TODO(API): await api.put(ENDPOINTS.settings(), payload)
});

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;

  if (action === "test-connection") {
    console.log("[settings] test-connection", { endpoint: ENDPOINTS.llmHealth() });
    // TODO(API): await api.get(ENDPOINTS.llmHealth())
  }

  if (action === "diagnose-llm") {
    console.log("[settings] diagnose-llm", { endpoint: ENDPOINTS.llmDiagnose() });
    // TODO(API): await api.post(ENDPOINTS.llmDiagnose()) → 更新 #capability-profile-grid
  }
});

console.log("[settings] page module loaded", { api });
