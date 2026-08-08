// lib/query-workbench.js — 唯讀查詢工作台（DB Agent 頁的「查詢資料」分頁）
//
// 兩種提問方式共用同一個結果區：
//   「用中文問」→ /workbench/nl2sql 產生 SQL → 直接執行 → 結果 + 白話說明，
//                 SQL 收在「進階」摺疊區（不會寫 SQL 的人不必看，要覆核的人點得開）
//   「自己寫 SQL」→ /workbench/query 直接執行
//
// 兩條路徑產出的結果都掛上可信度標記：查詢語法是 AI 生成或人自己寫的，都還沒有
// 第二個人覆核過，而這些數字常常會被貼進給主管看的簡報。

import { ENDPOINTS, api } from "./api.js";
import { renderPlan } from "./explain-plan.js";
import { showToast } from "./toast.js";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** 值可能是 null／物件（JSON 欄位），一律轉成看得懂的字串。 */
function cellText(value) {
  if (value === null || value === undefined) return "∅";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function isNumeric(value) {
  return typeof value === "number";
}

/**
 * 結果轉 CSV。開頭的 BOM 是為了 Excel：沒有它，中文欄位在 Excel 開起來是亂碼
 * （Excel 不會自己猜 UTF-8）。有 BOM 的 .csv 雙擊就能正常開，不需要匯入精靈。
 */
function toCsv(columns, rows) {
  const escape = (v) => {
    const s = cellText(v).replace(/"/g, '""');
    return /[",\n]/.test(s) ? `"${s}"` : s;
  };
  const lines = [columns.map(escape).join(",")];
  for (const row of rows) lines.push(row.map(escape).join(","));
  return `﻿${lines.join("\r\n")}`;
}

function downloadCsv(columns, rows) {
  const blob = new Blob([toCsv(columns, rows)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `查詢結果_${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

export function createQueryWorkbench({ resultEl, getDbName, onSaved }) {
  let lastResult = null;
  let lastQuery = null;  // { question, sql }——「存成常用」要知道存什麼

  function renderEmpty(message) {
    resultEl.textContent = "";
    resultEl.appendChild(el("p", "form-hint", message));
  }

  function renderTable({ columns, rows, truncated }, { explanation, sql, approval } = {}) {
    lastResult = { columns, rows };
    resultEl.textContent = "";

    if (explanation) {
      const box = el("div", "workbench-explanation");
      box.appendChild(el("div", "workbench-explanation-title", "這是怎麼算出來的"));
      box.appendChild(el("p", null, explanation));
      resultEl.appendChild(box);
    }

    if (sql) {
      // <details> 預設收合：不會寫 SQL 的人看不到雜訊，要覆核的人點得開。
      const details = el("details", "workbench-sql-advanced");
      details.appendChild(el("summary", null, "進階：查看實際查詢語法"));
      details.appendChild(el("pre", "code-block", sql));
      resultEl.appendChild(details);
    }

    // 措辭要跟實際來源相符：SQL 模式的語法是使用者自己寫的，講成 AI 產生會失去可信度。
    if (approval && approval.approved && !approval.stale) {
      const ok = el(
        "p",
        "workbench-trust is-approved",
        `✓ 這個問題的口徑已由 ${approval.approved_by} 確認過（${(approval.approved_at || "").slice(0, 10)}）。`
      );
      resultEl.appendChild(ok);
    } else if (approval && approval.stale) {
      resultEl.appendChild(
        el(
          "p",
          "workbench-trust",
          `⚠ 這個問題上次確認口徑是 ${(approval.approved_at || "").slice(0, 10)}，已經有一段時間了。`
            + "資料表結構可能已經改過，建議請工程師或 DBA 重新確認一次。"
        )
      );
    } else {
      resultEl.appendChild(
        el(
          "p",
          "workbench-trust",
          sql
            ? "⚠ 這個結果由 AI 產生的查詢語法算出來，尚未經人工覆核。要放進對外報告前，請先找工程師或 DBA 確認口徑。"
            : "⚠ 這份查詢尚未經第二個人覆核。要放進對外報告前，建議先請人確認口徑。"
        )
      );
    }

    if (!rows.length) {
      resultEl.appendChild(el("p", "form-hint", "查詢成功，但沒有符合條件的資料。"));
      return;
    }

    const bar = el("div", "data-table-header-bar");
    bar.appendChild(el("span", "form-hint", `${rows.length} 筆${truncated ? "（僅顯示前 200 筆）" : ""}`));
    const exportBtn = el("button", "btn btn-ghost btn-sm", "⬇ 下載 CSV（Excel 可直接開）");
    exportBtn.type = "button";
    exportBtn.dataset.action = "export-query-result";
    bar.appendChild(exportBtn);

    if (lastQuery) {
      const save = el("button", "btn btn-ghost btn-sm", "⭐ 存成常用問題");
      save.type = "button";
      save.dataset.action = "save-question";
      bar.appendChild(save);
    }
    resultEl.appendChild(bar);

    const wrap = el("div", "data-table-wrap");
    const table = el("table", "data-table");
    const thead = el("thead");
    const headRow = el("tr");
    for (const name of columns) headRow.appendChild(el("th", null, name));
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = el("tbody");
    for (const row of rows) {
      const tr = el("tr");
      row.forEach((value) => {
        tr.appendChild(el("td", isNumeric(value) ? "data-table-numeric" : null, cellText(value)));
      });
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
    resultEl.appendChild(wrap);
  }

  async function runSql(sql) {
    renderEmpty("查詢中…");
    try {
      const result = await api.post(ENDPOINTS.workbenchDbQuery(), {
        sql,
        db_name: getDbName(),
      });
      lastQuery = { question: sql, sql };
      renderTable(result);
    } catch (err) {
      renderEmpty(err.detail || "查詢沒有成功。");
    }
  }

  /** 中文提問：先產生 SQL，再直接執行——使用者只按一次按鈕就看得到結果。 */
  async function runAsk(question) {
    renderEmpty("正在看懂你的問題…");
    let draft;
    try {
      draft = await api.post(ENDPOINTS.workbenchDbNl2sql(), {
        question,
        db_name: getDbName(),
      });
    } catch (err) {
      renderEmpty(err.detail || "沒辦法把這個問題轉成查詢，換個說法試試。");
      return;
    }

    renderEmpty("查詢中…");
    try {
      const result = await api.post(ENDPOINTS.workbenchDbQuery(), {
        sql: draft.sql,
        db_name: getDbName(),
      });
      lastQuery = { question, sql: draft.sql };
      renderTable(result, { explanation: draft.explanation, sql: draft.sql });
    } catch (err) {
      // SQL 產出來了但跑不動：把語法秀出來，工程師才有東西可以接手。
      resultEl.textContent = "";
      resultEl.appendChild(el("p", "form-hint", err.detail || "這個查詢執行失敗了。"));
      const details = el("details", "workbench-sql-advanced");
      details.open = true;
      details.appendChild(el("summary", null, "AI 產生的查詢語法（可以拿給工程師看）"));
      details.appendChild(el("pre", "code-block", draft.sql));
      resultEl.appendChild(details);
    }
  }

  /** 執行計畫：畫成樹狀，掃全表等地雷附一句白話說明。 */
  async function runPlan(sql) {
    renderEmpty("取得執行計畫中…");
    try {
      const result = await api.post(ENDPOINTS.workbenchDbExplain(), {
        sql,
        db_name: getDbName(),
      });
      lastResult = null;  // 計畫不是查詢結果，不能被「下載」誤用
      resultEl.textContent = "";
      renderPlan(resultEl, result);
    } catch (err) {
      renderEmpty(err.detail || "沒辦法取得這個查詢的執行計畫。");
    }
  }

  function exportResult() {
    if (!lastResult || !lastResult.rows.length) return;
    downloadCsv(lastResult.columns, lastResult.rows);
    showToast("已下載，用 Excel 直接開啟即可", "success");
  }

  async function saveCurrentQuestion() {
    if (!lastQuery) return;
    try {
      await api.post(ENDPOINTS.workbenchSaveQuestion(), {
        db_name: getDbName() || "",
        question: lastQuery.question,
        sql: lastQuery.sql,
      });
      showToast("已存成常用問題，下次可以一鍵重跑", "success");
      if (onSaved) await onSaved();
    } catch {
      // apiFetch 已 toast
    }
  }

  /** 重跑一則存下來的問題——把它的核可狀態一起帶進結果，讓標記反映真實情況。 */
  async function runSaved(entry) {
    renderEmpty("查詢中…");
    try {
      const result = await api.post(ENDPOINTS.workbenchDbQuery(), {
        sql: entry.sql,
        db_name: getDbName(),
      });
      lastQuery = { question: entry.question, sql: entry.sql };
      renderTable(result, { sql: entry.sql, approval: entry });
    } catch (err) {
      renderEmpty(err.detail || "這個查詢執行失敗了。");
    }
  }

  return { runSql, runAsk, runPlan, exportResult, saveCurrentQuestion, runSaved };
}
