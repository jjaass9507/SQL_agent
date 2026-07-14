// lib/drawer.js — 全站 DB Agent 浮動抽屜
//
// 所有頁面右下角浮動按鈕展開的對話抽屜，共用 lib/agent-chat.js 的對話邏輯
// （第一波前端 agent 回報的缺口）。在 /agent 完整頁本身不掛載，避免與
// 該頁已有的完整 DB Agent UI 重複。

import { createAgentChat } from "./agent-chat.js";
import { showToast } from "./toast.js";

export function mountAgentDrawer() {
  if (document.body.dataset.activePage === "agent") return;

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "agent-drawer-toggle";
  toggle.setAttribute("aria-label", "開啟 DB Agent 對話");
  toggle.dataset.action = "toggle-agent-drawer";
  toggle.textContent = "💬";

  const panel = document.createElement("div");
  panel.className = "agent-drawer-panel card";
  panel.hidden = true;
  panel.innerHTML = `
    <div class="agent-drawer-header">
      <span class="card-title">DB Agent</span>
      <button type="button" class="btn btn-ghost btn-sm" data-action="toggle-agent-drawer">✕</button>
    </div>
    <div class="chat-messages agent-drawer-messages" data-target="agent-drawer-messages">
      <div class="chat-bubble-row">
        <div class="chat-bubble chat-bubble-ai">你好！我是資料庫助手，可以隨時問我資料表結構或討論異動。</div>
      </div>
    </div>
    <form class="chat-input-row" data-action="agent-drawer-send">
      <textarea class="form-textarea" rows="1" placeholder="輸入訊息…" data-target="agent-drawer-input"></textarea>
      <button type="submit" class="btn btn-primary btn-sm" data-action="agent-drawer-submit">送出</button>
    </form>
  `;

  document.body.appendChild(toggle);
  document.body.appendChild(panel);

  const messagesEl = panel.querySelector('[data-target="agent-drawer-messages"]');
  const input = panel.querySelector('[data-target="agent-drawer-input"]');
  const submitBtn = panel.querySelector('[data-action="agent-drawer-submit"]');

  const chat = createAgentChat({
    messagesEl,
    onLockChange: (locked) => {
      input.disabled = locked;
      submitBtn.disabled = locked;
    },
    onTurnDone: (data) => {
      if (data.proposal) {
        const shortId = String(data.proposal.proposal_id || "").slice(0, 8);
        showToast(`已提交變更提案（#${shortId}），請至 DB Agent 頁審核`, "info");
      }
      if (data.design_request) {
        showToast("已記錄新資料表設計需求，請至 DB Agent 頁查看詳情", "info");
      }
    },
  });

  document.addEventListener("click", (event) => {
    const target = event.target.closest('[data-action="toggle-agent-drawer"]');
    if (!target) return;
    panel.hidden = !panel.hidden;
  });

  panel.addEventListener("submit", (event) => {
    if (!event.target.matches('[data-action="agent-drawer-send"]')) return;
    event.preventDefault();
    const message = input.value.trim();
    if (!message) return;
    chat.send(message);
    input.value = "";
  });

  panel.addEventListener("keydown", (event) => {
    if (event.target === input && event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      event.target.closest("form").requestSubmit();
    }
  });
}

mountAgentDrawer();
