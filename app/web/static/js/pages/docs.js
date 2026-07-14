// pages/docs.js — 文件查閱頁：outputs 分頁預覽、下載、extras 觸發、events SSE 生成進度
import { ENDPOINTS, api } from "../lib/api.js";
import { connectSSE } from "../lib/sse.js";
import { showToast } from "../lib/toast.js";

const layout = document.querySelector("[data-session-id]");
const sessionId = layout ? layout.dataset.sessionId : null;

// 分頁 key → 檔名對照（與 generation_service.FILENAMES 一致）
const TAB_FILES = {
  spec: "01_specification.md",
  er_diagram: "02_er_diagram.md",
  ddl: "03_ddl.sql",
  security_plan: "04_security_plan.md",
};
const CORE_FILENAMES = new Set(Object.values(TAB_FILES));

// filename → content 快取（下載單檔、切換分頁用）
const outputsCache = new Map();
let activeTab = "spec";
let eventsSource = null;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// ── 生成進度卡（events SSE：generation_status → 每檔狀態） ──────────────

const STEP_ICON = { waiting: "○", loading: "◐", done: "✓", failed: "✗" };

function renderProgress(progress) {
  for (const item of document.querySelectorAll('[data-target="progress-item"]')) {
    const state = (progress || {})[item.dataset.file] || "waiting";
    item.className = `progress-step is-${state === "loading" ? "active" : state}`;
    const icon = item.querySelector(".progress-step-icon");
    if (icon) icon.textContent = STEP_ICON[state] || "○";
  }
}

function setStatusPill(status) {
  const pill = document.querySelector('[data-target="generation-status"]');
  if (!pill) return;
  const map = {
    queued: ["in_progress", "排隊中"],
    running: ["in_progress", "產出中"],
    done: ["done", "已完成"],
    failed: ["failed", "產出失敗"],
  };
  const [cls, text] = map[status] || ["in_progress", status];
  pill.className = `status-pill status-pill-${cls}`;
  pill.textContent = text;
}

// 監看生成 job 進度；job 進終態後結束並重新載入 outputs
function watchGenerationJob(jobId) {
  if (eventsSource) eventsSource.close();
  eventsSource = connectSSE(ENDPOINTS.sessionEvents(sessionId, jobId), {
    onEvent: (name, data) => {
      if (name !== "generation_status") return;
      setStatusPill(data.status);
      if (data.progress) renderProgress(data.progress);
      if (data.status === "done" || data.status === "failed") {
        eventsSource.close();
        eventsSource = null;
        if (data.status === "failed" && data.error) {
          showToast(`產出失敗：${data.error}`, "error");
        }
        loadOutputs();
      }
    },
    onGiveUp: () => showToast("連線中斷，請重新整理", "error"),
  });
}

// ── 文件內容渲染 ────────────────────────────────────────────────────────

// 從 markdown 抽出 ```mermaid 圍欄區塊，回傳 [mermaid 原始碼或 null, 其餘文字]
function extractMermaid(markdown) {
  const match = markdown.match(/```mermaid\n([\s\S]*?)```/);
  if (!match) return [null, markdown];
  return [match[1], markdown.replace(match[0], "").trim()];
}

function renderMarkdownPanel(target, content) {
  const panel = document.querySelector(`[data-target="doc-content-${target}"]`);
  if (!panel) return;
  panel.textContent = "";
  const body = el("div", "doc-markdown", content);
  panel.appendChild(body);
}

async function renderErDiagramPanel(content) {
  const panel = document.querySelector('[data-target="doc-content-er_diagram"]');
  if (!panel) return;
  panel.textContent = "";

  const [mermaidCode, rest] = extractMermaid(content);
  if (mermaidCode && window.mermaid) {
    const holder = document.createElement("pre");
    holder.className = "mermaid";
    holder.textContent = mermaidCode;
    panel.appendChild(holder);
    try {
      window.mermaid.initialize({ startOnLoad: false });
      await window.mermaid.run({ nodes: [holder] });
    } catch {
      // Mermaid 語法渲染失敗時保留原始碼文字
    }
  } else if (mermaidCode) {
    // CDN 未載入（離線環境）時降級顯示原始碼
    panel.appendChild(el("pre", "code-block", mermaidCode));
  }
  if (rest) panel.appendChild(el("div", "doc-markdown", rest));
}

function renderDdlPanel(content) {
  const pre = document.querySelector('[data-target="doc-content-ddl"]');
  if (pre) pre.textContent = content;
}

// 非四份核心文件的產出（extras）→ 延伸產出清單
function renderExtrasList() {
  const list = document.querySelector('[data-target="extras-list"]');
  if (!list) return;
  list.textContent = "";
  const extras = [...outputsCache.keys()].filter((f) => !CORE_FILENAMES.has(f));
  if (!extras.length) {
    list.appendChild(el("p", "form-hint", "尚無延伸產出，點上方按鈕觸發。"));
    return;
  }
  for (const filename of extras) {
    const row = el("div", "docs-extra-row");
    row.appendChild(el("span", null, filename));
    const btn = el("button", "btn btn-ghost btn-sm", "下載");
    btn.type = "button";
    btn.dataset.action = "download-extra";
    btn.dataset.target = filename;
    row.appendChild(btn);
    list.appendChild(row);
  }
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

  if (outputsCache.has(TAB_FILES.spec)) renderMarkdownPanel("spec", outputsCache.get(TAB_FILES.spec));
  if (outputsCache.has(TAB_FILES.er_diagram)) renderErDiagramPanel(outputsCache.get(TAB_FILES.er_diagram));
  if (outputsCache.has(TAB_FILES.ddl)) renderDdlPanel(outputsCache.get(TAB_FILES.ddl));
  if (outputsCache.has(TAB_FILES.security_plan)) {
    renderMarkdownPanel("security_plan", outputsCache.get(TAB_FILES.security_plan));
  }
  renderExtrasList();
}

// ── 下載 ────────────────────────────────────────────────────────────────

function downloadFile(filename) {
  const content = outputsCache.get(filename);
  if (content === undefined) {
    showToast("此文件尚未產出", "warning");
    return;
  }
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

// ── 事件委派 ────────────────────────────────────────────────────────────

document.addEventListener("click", async (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;

  if (action === "switch-tab") {
    activeTab = target.dataset.target;
    document.querySelectorAll('[data-action="switch-tab"]').forEach((tab) => {
      tab.classList.toggle("is-active", tab === target);
    });
    document.querySelectorAll('[data-target="doc-panel"]').forEach((panel) => {
      panel.classList.toggle("is-active", panel.dataset.panel === activeTab);
    });
  }

  if (action === "copy-code") {
    const codeEl = document.querySelector(`[data-target="doc-content-${target.dataset.target}"]`);
    if (codeEl && navigator.clipboard) {
      navigator.clipboard.writeText(codeEl.textContent);
      showToast("已複製", "success");
    }
  }

  if (action === "download-single") {
    downloadFile(TAB_FILES[activeTab]);
  }

  if (action === "download-extra") {
    downloadFile(target.dataset.target);
  }

  if (action === "download-all") {
    window.location.href = ENDPOINTS.sessionOutputsZip(sessionId);
  }

  if (action === "generate-extra") {
    const kind = target.dataset.kind;
    target.disabled = true;
    try {
      const result = await api.post(ENDPOINTS.sessionExtraGenerate(sessionId, kind));
      showToast("已排入產出佇列", "info");
      // 接 events SSE 追蹤 extra job，終態後重新載入 outputs
      const extraSource = connectSSE(ENDPOINTS.sessionEvents(sessionId, result.job_id), {
        onEvent: (name, data) => {
          if (name !== "generation_status") return;
          if (data.status === "done" || data.status === "failed") {
            extraSource.close();
            target.disabled = false;
            if (data.status === "failed") {
              showToast(`延伸產出失敗：${data.error || ""}`, "error");
            } else {
              showToast("延伸產出完成", "success");
              loadOutputs();
            }
          }
        },
        onGiveUp: () => {
          target.disabled = false;
          showToast("連線中斷，請重新整理", "error");
        },
      });
    } catch {
      target.disabled = false;
    }
  }
});

// ── 初始化：依最新 generate job 狀態決定「監看進度」或「直接載入文件」 ──

async function init() {
  if (!sessionId) return;
  let detail;
  try {
    detail = await api.get(ENDPOINTS.session(sessionId));
  } catch {
    return;
  }

  // jobs 已依 created_at 新到舊排序
  const generateJob = (detail.jobs || []).find((j) => j.kind === "generate");
  if (generateJob) {
    setStatusPill(generateJob.status);
    if (generateJob.progress_json) renderProgress(generateJob.progress_json);
    if (generateJob.status === "queued" || generateJob.status === "running") {
      watchGenerationJob(generateJob.id);
      return;
    }
  }
  loadOutputs();
}

init();
