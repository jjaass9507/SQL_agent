// lib/schema-browser.js — 結構瀏覽器 + 資料字典（工作台的「找資料表」模式）
//
// 三個角色要的其實是同一個東西，這裡合併成一份：
//   後端工程師：對照現有表的命名慣例與型態，避免撞名或不一致
//   DBA：把「這張表是幹嘛的」「這欄位誰在用」的答案留在系統裡，不要每次重講
//   業務單位：用中文找得到「退貨資料在哪張表」，不必先看懂 schema
//
// 因此搜尋同時比對表名、欄位名與註記內容——業務單位打「退貨」，命中的會是
// DBA 寫的中文說明；工程師打 `member_id`，命中的是欄位名。

import { ENDPOINTS, api } from "./api.js";
import { showToast } from "./toast.js";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function createSchemaBrowser({ containerEl, getDbName }) {
  let tables = [];
  let dictionary = {};
  let keyword = "";
  const expanded = new Set();

  const noteOf = (table, column) => dictionary[column ? `${table}|${column}` : table] || {};

  /** 表名、欄位名、註記文字都要能被搜到——三種使用者用的關鍵字不一樣。 */
  function matches(table) {
    if (!keyword) return true;
    const needle = keyword.toLowerCase();
    if (table.name.toLowerCase().includes(needle)) return true;
    if ((noteOf(table.name).note || "").toLowerCase().includes(needle)) return true;
    return table.columns.some(
      (c) =>
        c.name.toLowerCase().includes(needle) ||
        (noteOf(table.name, c.name).note || "").toLowerCase().includes(needle)
    );
  }

  function renderColumnRow(tableName, column) {
    const entry = noteOf(tableName, column.name);
    const row = el("div", "schema-column");

    const head = el("div", "schema-column-head");
    head.appendChild(el("span", "schema-column-name", column.name));
    const badges = el("span", "schema-column-badges");
    if (column.is_pk) badges.appendChild(el("span", "schema-badge is-pk", "主鍵"));
    if (column.is_fk) {
      badges.appendChild(
        el("span", "schema-badge is-fk", column.fk_table ? `→ ${column.fk_table}` : "外鍵")
      );
    }
    if (!column.nullable) badges.appendChild(el("span", "schema-badge", "必填"));
    head.appendChild(badges);
    head.appendChild(el("span", "schema-column-type", column.type || ""));
    row.appendChild(head);

    if (entry.note) {
      const note = el("p", "schema-note", entry.note);
      if (entry.owner) note.appendChild(el("span", "schema-note-owner", `（負責人：${entry.owner}）`));
      row.appendChild(note);
    }

    const edit = el("button", "btn btn-ghost btn-sm schema-note-edit", entry.note ? "改說明" : "加說明");
    edit.type = "button";
    edit.dataset.action = "edit-dictionary";
    edit.dataset.table = tableName;
    edit.dataset.column = column.name;
    row.appendChild(edit);
    return row;
  }

  function render() {
    containerEl.textContent = "";

    const search = el("input", "form-input");
    search.type = "search";
    search.placeholder = "輸入你想找的東西，例如：退貨、訂單、member_id";
    search.value = keyword;
    search.dataset.target = "schema-search";
    containerEl.appendChild(search);

    const visible = tables.filter(matches);
    if (!tables.length) {
      containerEl.appendChild(el("p", "form-hint", "讀不到這個資料庫的結構。"));
      return;
    }
    if (!visible.length) {
      containerEl.appendChild(el("p", "form-hint", `找不到跟「${keyword}」有關的資料表。`));
      return;
    }

    const list = el("div", "schema-list");
    for (const table of visible) {
      const entry = noteOf(table.name);
      const card = el("div", "schema-table-card");

      const header = el("button", "schema-table-head");
      header.type = "button";
      header.dataset.action = "toggle-table";
      header.dataset.table = table.name;
      // 業務單位看不懂英文表名，所以有中文說明時讓說明當標題、表名退成小字。
      if (entry.note) {
        header.appendChild(el("span", "schema-table-title", entry.note));
        header.appendChild(el("span", "schema-table-sub", `${table.name}・${table.columns.length} 個欄位`));
      } else {
        header.appendChild(el("span", "schema-table-title", table.name));
        header.appendChild(
          el("span", "schema-table-sub", `${table.columns.length} 個欄位・尚未填寫說明`)
        );
      }
      card.appendChild(header);

      if (expanded.has(table.name)) {
        const body = el("div", "schema-table-body");
        const tableEdit = el(
          "button",
          "btn btn-ghost btn-sm",
          entry.note ? "改這張表的說明" : "幫這張表加說明"
        );
        tableEdit.type = "button";
        tableEdit.dataset.action = "edit-dictionary";
        tableEdit.dataset.table = table.name;
        body.appendChild(tableEdit);
        for (const column of table.columns) body.appendChild(renderColumnRow(table.name, column));
        card.appendChild(body);
      }
      list.appendChild(card);
    }
    containerEl.appendChild(list);
  }

  async function load() {
    containerEl.textContent = "";
    containerEl.appendChild(el("p", "form-hint", "讀取資料庫結構中…"));
    const dbName = getDbName();
    try {
      const [tree, dict] = await Promise.all([
        api.get(ENDPOINTS.workbenchDbSchemaTree(dbName)),
        api.get(ENDPOINTS.workbenchDbDictionary(dbName), { silent: true }).catch(() => ({})),
      ]);
      tables = tree.tables || [];
      dictionary = dict || {};
      render();
    } catch (err) {
      containerEl.textContent = "";
      containerEl.appendChild(el("p", "form-hint", err.detail || "讀不到資料庫結構。"));
    }
  }

  async function saveEntry(table, column, note, owner) {
    try {
      const entry = await api.put(ENDPOINTS.workbenchDbDictionary(""), {
        db_name: getDbName() || "",
        table,
        column: column || null,
        note,
        owner,
      });
      const key = column ? `${table}|${column}` : table;
      if (entry && entry.note) dictionary[key] = entry;
      else delete dictionary[key];
      render();
      showToast("說明已儲存，其他人也看得到", "success");
    } catch {
      // apiFetch 已 toast
    }
  }

  function handleSearch(value) {
    keyword = value.trim();
    render();
    const box = containerEl.querySelector('[data-target="schema-search"]');
    if (box) {
      box.focus();
      box.setSelectionRange(box.value.length, box.value.length);
    }
  }

  function toggleTable(name) {
    if (expanded.has(name)) expanded.delete(name);
    else expanded.add(name);
    render();
  }

  return { load, render, saveEntry, handleSearch, toggleTable, noteOf };
}
