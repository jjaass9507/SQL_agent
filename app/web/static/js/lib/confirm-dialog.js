// lib/confirm-dialog.js — 破壞性操作的確認對話框（回傳 Promise<boolean>）
//
// 刻意不用原生 window.confirm()：它只能顯示一段純文字，而使用者實測回饋是
// 「你確定嗎」這五個字沒有幫助——他們要知道的是「會影響哪個系統、能不能
// 復原、不確定該找誰」以及要執行的內容本身。
//
// 樣式沿用 components.css 既有的 .modal-overlay / .modal / .modal-actions。

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/**
 * @param {object} options
 * @param {string} options.title 標題
 * @param {string} options.lead 開頭一句話，講清楚按下去會發生什麼
 * @param {{label: string, value: string, tone?: "warn"}[]} [options.facts] 條列事實
 * @param {string} [options.code] 要讓使用者最後看一眼的內容（例如 DDL 全文）
 * @param {string} [options.ackLabel] 有值時顯示必勾的確認框，未勾則確認鈕不可按
 * @param {string} [options.confirmText] 確認鈕文字
 * @param {boolean} [options.danger] 確認鈕是否用危險色
 * @returns {Promise<boolean>}
 */
export function confirmDialog({
  title,
  lead,
  facts = [],
  code,
  ackLabel,
  confirmText = "確認",
  danger = false,
}) {
  return new Promise((resolve) => {
    const overlay = el("div", "modal-overlay");
    const modal = el("div", "modal confirm-modal");
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");

    modal.appendChild(el("h2", "modal-title", title));
    if (lead) modal.appendChild(el("p", "confirm-lead", lead));

    if (facts.length) {
      const list = el("dl", "confirm-facts");
      for (const fact of facts) {
        list.appendChild(el("dt", null, fact.label));
        list.appendChild(el("dd", fact.tone === "warn" ? "is-warn" : null, fact.value));
      }
      modal.appendChild(list);
    }

    if (code) modal.appendChild(el("pre", "code-block confirm-code", code));

    let ackBox = null;
    if (ackLabel) {
      const wrap = el("label", "confirm-ack");
      ackBox = document.createElement("input");
      ackBox.type = "checkbox";
      wrap.appendChild(ackBox);
      wrap.appendChild(el("span", null, ackLabel));
      modal.appendChild(wrap);
    }

    const actions = el("div", "modal-actions");
    const cancel = el("button", "btn btn-ghost", "取消");
    cancel.type = "button";
    const confirm = el("button", `btn ${danger ? "btn-danger" : "btn-primary"}`, confirmText);
    confirm.type = "button";
    confirm.disabled = Boolean(ackBox);
    if (ackBox) ackBox.addEventListener("change", () => (confirm.disabled = !ackBox.checked));
    actions.appendChild(cancel);
    actions.appendChild(confirm);
    modal.appendChild(actions);

    function close(result) {
      document.removeEventListener("keydown", onKey);
      overlay.remove();
      resolve(result);
    }
    function onKey(event) {
      if (event.key === "Escape") close(false);
    }

    cancel.addEventListener("click", () => close(false));
    confirm.addEventListener("click", () => close(true));
    // 點背景等同取消；點對話框本身不要關掉
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) close(false);
    });
    document.addEventListener("keydown", onKey);

    overlay.appendChild(modal);
    document.body.appendChild(overlay);
    cancel.focus();
  });
}
