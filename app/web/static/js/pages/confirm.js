// pages/confirm.js — 需求確認頁：tables 表格 + key_points 摘要 + 版本還原 + 確認產出
import { ENDPOINTS, api } from "../lib/api.js";
import { showToast } from "../lib/toast.js";

const layout = document.querySelector("[data-session-id]");
const sessionId = layout ? layout.dataset.sessionId : null;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// ── 結構化 Schema 表格 ──────────────────────────────────────────────────

function renderTables(tables) {
  const container = document.querySelector('[data-target="schema-tables-container"]');
  if (!container) return;
  container.textContent = "";
  if (!tables || !tables.length) {
    container.appendChild(el("p", "form-hint", "尚無資料表，完成需求收集對話後將顯示於此。"));
    return;
  }

  for (const table of tables) {
    const wrap = el("div", "data-table-wrap");
    wrap.appendChild(
      el("div", "data-table-header-bar", `${table.table_name} — ${table.description || ""}`)
    );

    const tbl = el("table", "data-table");
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    for (const label of ["欄位", "型態", "NULL", "說明"]) {
      headRow.appendChild(el("th", null, label));
    }
    thead.appendChild(headRow);
    tbl.appendChild(thead);

    const tbody = document.createElement("tbody");
    for (const col of table.columns) {
      const row = document.createElement("tr");
      const nameCell = el(
        "td",
        `data-table-field-name${col.is_primary_key ? " is-pk" : ""}`,
        `${col.is_primary_key ? "🔑 " : ""}${col.name}`
      );
      const typeText = col.length ? `${col.data_type}(${col.length})` : col.data_type;
      row.appendChild(nameCell);
      row.appendChild(el("td", "data-table-field-type", typeText));
      row.appendChild(el("td", null, col.nullable ? "允許" : "NOT NULL"));
      const notes = [col.description || ""];
      if (col.is_foreign_key && col.references) notes.push(`FK → ${col.references}`);
      if (col.is_unique) notes.push("UNIQUE");
      row.appendChild(el("td", null, notes.filter(Boolean).join("；")));
      tbody.appendChild(row);
    }
    tbl.appendChild(tbody);
    wrap.appendChild(tbl);
    container.appendChild(wrap);
  }
}

function renderKeyPoints(keyPoints) {
  const list = document.querySelector('[data-target="requirement-summary-list"]');
  if (!list) return;
  list.textContent = "";
  if (!keyPoints || !keyPoints.length) {
    list.appendChild(el("li", "form-hint", "尚無摘要，完成需求收集對話後將顯示於此。"));
    return;
  }
  for (const point of keyPoints) {
    list.appendChild(el("li", null, point));
  }
}

// ── 與現有 DB 差異（後端 app/rules/schema_diff.py 算好，前端只負責呈現） ──
//
// 不要在這裡重寫比對邏輯：後端會比對型態／NULL／UNIQUE／索引，前端自己用欄位
// 名稱做集合差集會把 VARCHAR(20)→VARCHAR(10) 這種會截斷資料的變更判成「不變」。

function renderDiff(diff) {
  const container = document.querySelector('[data-target="schema-diff"]');
  if (!container) return;
  container.textContent = "";
  if (!diff) {
    container.appendChild(el("p", "form-hint", "此 session 未匯入現有 DB，無差異比對。"));
    return;
  }

  function addItem(tagClass, tagText, description) {
    const item = el("div", "diff-item");
    item.appendChild(el("span", `diff-tag diff-tag-${tagClass}`, tagText));
    item.appendChild(el("span", null, description));
    container.appendChild(item);
  }

  for (const name of diff.new_tables || []) addItem("new", "新增", name);

  for (const [name, detail] of Object.entries(diff.modified_tables || {})) {
    const parts = [];
    if (detail.added_columns?.length) {
      parts.push(`新增欄位：${detail.added_columns.map((c) => c.name).join(", ")}`);
    }
    if (detail.removed_columns?.length) {
      parts.push(`移除欄位：${detail.removed_columns.map((c) => c.name).join(", ")}`);
    }
    for (const col of detail.changed_columns || []) {
      parts.push(`${col.name}：${col.diffs.join("、")}`);
    }
    addItem("modified", "變更", `${name}（${parts.join("；")}）`);
  }

  for (const name of diff.unchanged_tables || []) addItem("same", "不變", name);
  for (const name of diff.dropped_tables || []) addItem("dropped", "移除", name);
}

// ── 版本列表／還原 ──────────────────────────────────────────────────────

async function loadVersions() {
  const select = document.querySelector('[data-target="version-select"]');
  if (!select) return;
  try {
    const versions = await api.get(ENDPOINTS.sessionVersions(sessionId), { silent: true });
    select.textContent = "";
    // 後端已依版本號由新到舊排序，直接依序渲染（最新版在最上）
    for (const version of versions) {
      const option = document.createElement("option");
      option.value = String(version.version_num);
      option.textContent = `v${version.version_num}（${new Date(version.created_at).toLocaleString()}）`;
      select.appendChild(option);
    }
  } catch {
    // 版本清單載入失敗不阻擋主流程
  }
}

async function loadDetail() {
  try {
    const detail = await api.get(ENDPOINTS.session(sessionId));
    renderTables(detail.latest_tables);
    renderKeyPoints(detail.latest_key_points);
    renderDiff(detail.schema_diff);
  } catch {
    // apiFetch 已 toast
  }
}

document.addEventListener("click", async (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;

  if (action === "confirm-generate") {
    target.disabled = true;
    try {
      await api.post(ENDPOINTS.sessionConfirm(sessionId));
      window.location.href = `/docs/${sessionId}`;
    } catch {
      target.disabled = false;
    }
  }

  if (action === "restore-version") {
    const select = document.querySelector('[data-target="version-select"]');
    const version = select ? select.value : null;
    if (!version) return;
    try {
      await api.post(ENDPOINTS.sessionVersionRestore(sessionId, version));
      showToast(`已還原至 v${version}`, "success");
      await Promise.all([loadDetail(), loadVersions()]);
    } catch {
      // apiFetch 已 toast
    }
  }
});

loadDetail();
loadVersions();
