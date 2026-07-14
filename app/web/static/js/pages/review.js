// pages/review.js — 審查報告頁：events SSE 追蹤審查進度、渲染 05 報告與 06 修復 SQL、下載
import { ENDPOINTS, api } from "../lib/api.js";
import { connectSSE } from "../lib/sse.js";
import { showToast } from "../lib/toast.js";

const layout = document.querySelector("[data-session-id]");
const sessionId = layout ? layout.dataset.sessionId : null;

const REPORT_FILE = "05_review_report.md";
const FIX_FILE = "06_review_fix.sql";

// 報告章節標題關鍵字 → 模板 data-target 後綴
const SECTION_KEYS = [
  ["設計一致性", "consistency"],
  ["資料完整性", "integrity"],
  ["效能", "performance"],
  ["安全", "security"],
];

const outputsCache = new Map();

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// ── 報告解析與渲染 ──────────────────────────────────────────────────────

// 解析「**整體評分：X/10**」；找不到時回傳 null
function parseScore(markdown) {
  const match = markdown.match(/整體評分[：:]\s*(\d+(?:\.\d+)?)\s*\/\s*10/);
  return match ? match[1] : null;
}

// 以 "## " 標題切段，依關鍵字對應到四個維度；回傳 {key: 條列文字[]}
function parseSections(markdown) {
  const sections = {};
  const parts = markdown.split(/\n(?=## )/);
  for (const part of parts) {
    const headingLine = part.split("\n", 1)[0];
    const matched = SECTION_KEYS.find(([keyword]) => headingLine.includes(keyword));
    if (!matched) continue;
    const items = part
      .split("\n")
      .filter((line) => line.trimStart().startsWith("- "))
      .map((line) => line.trimStart().slice(2).replaceAll("**", ""));
    sections[matched[1]] = items;
  }
  return sections;
}

function renderReport(markdown) {
  const score = parseScore(markdown);
  const scoreEl = document.querySelector('[data-target="review-score-value"]');
  if (scoreEl && score !== null) scoreEl.textContent = `${score}/10`;

  const summaryEl = document.querySelector('[data-target="review-summary"]');
  if (summaryEl) summaryEl.textContent = "審查完成，可下載完整報告。";

  const sections = parseSections(markdown);
  let anyRendered = false;
  for (const [, key] of SECTION_KEYS) {
    const list = document.querySelector(`[data-target="review-flags-${key}"]`);
    if (!list) continue;
    const items = sections[key];
    if (!items || !items.length) continue;
    anyRendered = true;
    list.textContent = "";
    for (const item of items) {
      list.appendChild(el("li", null, item));
    }
  }

  // 報告格式不符預期時降級：整份原文顯示在第一段
  if (!anyRendered) {
    const fallback = document.querySelector('[data-target="review-flags-consistency"]');
    if (fallback) {
      fallback.textContent = "";
      const raw = el("li");
      raw.appendChild(el("div", "doc-markdown", markdown));
      fallback.appendChild(raw);
    }
  }
}

function renderFixSql(content) {
  const container = document.querySelector('[data-target="review-red-flags-list"]');
  if (!container) return;
  container.textContent = "";
  if (!content.trim()) {
    container.appendChild(el("p", "form-hint", "規則掃描未發現紅旗項目。"));
    return;
  }
  container.appendChild(el("pre", "code-block", content));
  const btn = el("button", "btn btn-ghost btn-sm", "下載修復 SQL");
  btn.type = "button";
  btn.dataset.action = "download-fix-sql";
  container.appendChild(btn);
}

async function loadOutputs() {
  let outputs;
  try {
    outputs = await api.get(ENDPOINTS.sessionOutputs(sessionId));
  } catch {
    return;
  }
  outputsCache.clear();
  for (const output of outputs) {
    outputsCache.set(output.filename, output.content || "");
  }
  if (outputsCache.has(REPORT_FILE)) renderReport(outputsCache.get(REPORT_FILE));
  if (outputsCache.has(FIX_FILE)) renderFixSql(outputsCache.get(FIX_FILE));
}

// ── 審查進度（events SSE，事件化取代輪詢） ─────────────────────────────

function watchReviewJob(jobId) {
  const summaryEl = document.querySelector('[data-target="review-summary"]');
  if (summaryEl) summaryEl.textContent = "審查進行中，完成後將自動顯示報告…";

  const source = connectSSE(ENDPOINTS.sessionEvents(sessionId, jobId), {
    onEvent: (name, data) => {
      if (name !== "generation_status") return;
      if (data.status === "done") {
        source.close();
        loadOutputs();
      } else if (data.status === "failed") {
        source.close();
        if (summaryEl) summaryEl.textContent = `審查失敗：${data.error || "未知錯誤"}`;
        showToast(`審查失敗：${data.error || ""}`, "error");
      }
    },
    onGiveUp: () => showToast("連線中斷，請重新整理", "error"),
  });
}

function downloadFile(filename) {
  const content = outputsCache.get(filename);
  if (content === undefined) {
    showToast("報告尚未產出", "warning");
    return;
  }
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;

  if (target.dataset.action === "download-report") downloadFile(REPORT_FILE);
  if (target.dataset.action === "download-fix-sql") downloadFile(FIX_FILE);
});

async function init() {
  if (!sessionId) return;
  let detail;
  try {
    detail = await api.get(ENDPOINTS.session(sessionId));
  } catch {
    return;
  }

  const reviewJob = (detail.jobs || []).find((j) => j.kind === "review");
  if (reviewJob && (reviewJob.status === "queued" || reviewJob.status === "running")) {
    watchReviewJob(reviewJob.id);
    return;
  }
  loadOutputs();
}

init();
