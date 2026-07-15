// login.js — 登入頁：帳密登入 / SSO 探測 / 登入成功後導向 next
//
// 對齊後端契約（見 docs/deployment.md「Windows Server IIS 部署（AD SSO）」與
// app/api/routers/auth.py）：POST /auth/login、GET /auth/me、GET /auth/sso。
// 後端 AD 驗證分支尚未合併時，/auth/me、/auth/sso 可能回 404——一律視為
// 「未登入 / SSO 不可用」並留在登入頁，不視為錯誤。

import { ENDPOINTS, api } from "./lib/api.js";

/** 只允許站內相對路徑（防 open redirect）；其餘一律導回首頁。 */
function sanitizeNext(rawNext) {
  if (!rawNext) return "/";
  if (rawNext.startsWith("/") && !rawNext.startsWith("//") && !rawNext.includes("://")) {
    return rawNext;
  }
  return "/";
}

const nextUrl = sanitizeNext(new URLSearchParams(window.location.search).get("next"));

const errorEl = document.querySelector('[data-target="login-error"]');
const ssoBtn = document.querySelector('[data-target="sso-login-btn"]');
const ssoDivider = document.querySelector('[data-target="sso-divider"]');

function showError(message) {
  if (!errorEl) return;
  errorEl.textContent = message;
  errorEl.hidden = false;
}

function hideError() {
  if (!errorEl) return;
  errorEl.hidden = true;
  errorEl.textContent = "";
}

// 已登入（/auth/me 回非 anonymous 的使用者資料）時直接導向 next，不重複顯示登入表單。
(async function redirectIfAlreadyAuthenticated() {
  try {
    const me = await api.get(ENDPOINTS.authMe(), { silent: true });
    if (me && !me.anonymous) {
      window.location.href = nextUrl;
    }
  } catch {
    // 401／端點尚未實作／網路錯誤：留在登入頁顯示表單
  }
})();

// SSO 按鈕：探測 /auth/sso 是否存在才顯示（redirect: "manual" 避免真的觸發導頁）。
(async function probeSso() {
  if (!ssoBtn) return;
  try {
    const resp = await fetch(ENDPOINTS.authSso(), { method: "GET", redirect: "manual" });
    if (resp.status === 404) return;
    ssoBtn.hidden = false;
    if (ssoDivider) ssoDivider.hidden = false;
  } catch {
    // 端點不存在或網路錯誤：保持隱藏
  }
})();

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  if (target.dataset.action === "sso-login") {
    window.location.href = ENDPOINTS.authSso();
  }
});

document.addEventListener("submit", async (event) => {
  const form = event.target;
  if (!form.matches('[data-action="login-submit"]')) return;
  event.preventDefault();
  hideError();

  const username = document.querySelector('[data-target="login-username"]').value.trim();
  const password = document.querySelector('[data-target="login-password"]').value;
  if (!username || !password) {
    showError("請輸入帳號與密碼");
    return;
  }

  const submitBtn = form.querySelector('button[type="submit"]');
  if (submitBtn) submitBtn.disabled = true;
  try {
    await api.post(ENDPOINTS.authLogin(), { username, password }, { silent: true });
    window.location.href = nextUrl;
  } catch (err) {
    if (err.status === 401) {
      showError("帳號或密碼錯誤");
    } else if (err.status === 429) {
      showError("嘗試次數過多，請稍後再試");
    } else {
      showError("登入失敗，請稍後再試");
    }
  } finally {
    if (submitBtn) submitBtn.disabled = false;
  }
});
