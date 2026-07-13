// lib/toast.js — 輕量 toast 提示（掛在 base.html 的 #toast-stack）
//
// 供各頁 module 共用，例如 SSE 斷線重試 3 次後顯示「請重新整理」（NFR-02）。

/**
 * @param {string} message
 * @param {"info"|"success"|"warning"|"error"} [level]
 */
export function showToast(message, level = "info") {
  const stack = document.getElementById("toast-stack");
  if (!stack) {
    console.warn("[toast] #toast-stack 不存在", message);
    return;
  }

  const el = document.createElement("div");
  el.className = level === "info" ? "toast" : `toast toast-${level}`;
  el.textContent = message;
  stack.appendChild(el);

  setTimeout(() => el.remove(), 6000);
}
