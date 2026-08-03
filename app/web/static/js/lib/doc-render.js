// lib/doc-render.js — 文件面板的渲染：Markdown → DOM、SQL 關鍵字上色
//
// 為什麼自己寫而不是拉一個 Markdown 函式庫：要渲染的內容是自家 writer 產的
// （app/rules/writers/spec_writer.py 決定表格與標題形狀，LLM writer 受 prompt
// 約束），語法範圍固定在標題／表格／條列／程式碼區塊／粗體與行內 code 這幾種。
// 一支通用 parser 換來的是一個 200KB 的相依，以及一條把字串當 HTML 塞進 DOM 的路徑。
//
// 這裡全程 createElement + textContent，因此 LLM 產出的內容就算含有 HTML 標籤，
// 也只會被當成文字顯示，不會被瀏覽器執行。tests/web/test_contract.py 會盯著這件事。

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// 行內語法：**粗體** 與 `code`。回傳 DOM 節點陣列，供呼叫端 append。
function inlineNodes(text) {
  const nodes = [];
  const re = /(\*\*(.+?)\*\*|`(.+?)`)/g;
  let last = 0;
  let match;
  while ((match = re.exec(text)) !== null) {
    if (match.index > last) nodes.push(document.createTextNode(text.slice(last, match.index)));
    if (match[2] !== undefined) nodes.push(el("strong", null, match[2]));
    else nodes.push(el("code", "md-code", match[3]));
    last = match.index + match[0].length;
  }
  if (last < text.length) nodes.push(document.createTextNode(text.slice(last)));
  return nodes;
}

function appendInline(parent, text) {
  for (const node of inlineNodes(text)) parent.appendChild(node);
  return parent;
}

const HEADING_RE = /^(#{1,4})\s+(.*)$/;
const TABLE_ROW_RE = /^\s*\|(.*)\|\s*$/;
// |---|:---:|---| 這種分隔列，不是資料列
const TABLE_SEP_RE = /^\s*\|[\s:|-]+\|\s*$/;
const LIST_RE = /^\s*[-*]\s+(.*)$/;
const ORDERED_RE = /^\s*\d+\.\s+(.*)$/;

function splitRow(line) {
  return line
    .replace(/^\s*\|/, "")
    .replace(/\|\s*$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

/**
 * 把 Markdown 文字渲染進 container（會先清空）。
 * 支援：# ~ #### 標題、| 表格、- / 1. 條列、``` 程式碼區塊、**粗體**、`行內 code`。
 * 其餘文字一律當段落，不會消失。
 */
export function renderMarkdown(container, markdown) {
  container.textContent = "";
  const lines = (markdown || "").split("\n");
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (line.trim().startsWith("```")) {
      const lang = line.trim().slice(3).trim();
      const body = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        body.push(lines[i]);
        i += 1;
      }
      i += 1; // 收尾的 ```
      const pre = el("pre", "code-block");
      if (lang.toLowerCase() === "sql") highlightSql(pre, body.join("\n"));
      else pre.textContent = body.join("\n");
      container.appendChild(pre);
      continue;
    }

    const heading = line.match(HEADING_RE);
    if (heading) {
      container.appendChild(
        appendInline(el(`h${Math.min(heading[1].length + 1, 6)}`, "md-heading"), heading[2])
      );
      i += 1;
      continue;
    }

    if (TABLE_ROW_RE.test(line) && !TABLE_SEP_RE.test(line)) {
      const rows = [];
      while (i < lines.length && TABLE_ROW_RE.test(lines[i])) {
        if (!TABLE_SEP_RE.test(lines[i])) rows.push(splitRow(lines[i]));
        i += 1;
      }
      const table = el("table", "md-table");
      const head = el("thead");
      const headRow = el("tr");
      for (const cell of rows[0]) headRow.appendChild(appendInline(el("th"), cell));
      head.appendChild(headRow);
      table.appendChild(head);
      const body = el("tbody");
      for (const row of rows.slice(1)) {
        const tr = el("tr");
        for (const cell of row) tr.appendChild(appendInline(el("td"), cell));
        body.appendChild(tr);
      }
      table.appendChild(body);
      container.appendChild(el("div", "md-table-wrap")).appendChild(table);
      continue;
    }

    const isList = LIST_RE.test(line) || ORDERED_RE.test(line);
    if (isList) {
      const ordered = ORDERED_RE.test(line) && !LIST_RE.test(line);
      const list = el(ordered ? "ol" : "ul", "md-list");
      while (i < lines.length) {
        const match = lines[i].match(ordered ? ORDERED_RE : LIST_RE);
        if (!match) break;
        list.appendChild(appendInline(el("li"), match[1]));
        i += 1;
      }
      container.appendChild(list);
      continue;
    }

    if (!line.trim()) {
      i += 1;
      continue;
    }

    // 其餘連續的非空行合併成一個段落
    const paragraph = [];
    while (i < lines.length && lines[i].trim() && !HEADING_RE.test(lines[i])
           && !TABLE_ROW_RE.test(lines[i]) && !LIST_RE.test(lines[i])
           && !lines[i].trim().startsWith("```")) {
      paragraph.push(lines[i]);
      i += 1;
    }
    container.appendChild(appendInline(el("p", "md-paragraph"), paragraph.join(" ")));
  }
}

// ── SQL 上色 ────────────────────────────────────────────────────────────

const SQL_KEYWORDS = [
  "CREATE", "TABLE", "INDEX", "UNIQUE", "ALTER", "ADD", "COLUMN", "CONSTRAINT",
  "PRIMARY", "FOREIGN", "KEY", "REFERENCES", "NOT", "NULL", "DEFAULT", "CHECK",
  "COMMENT", "ON", "IS", "SELECT", "FROM", "WHERE", "INSERT", "INTO", "VALUES",
  "DROP", "IF", "EXISTS", "AND", "OR", "AS", "ORDER", "BY", "GROUP", "JOIN",
  "LEFT", "INNER", "LIMIT", "BEGIN", "COMMIT", "SET",
];
// 註解 / 字串 / 關鍵字 / 數字，各自成組以便分類上色
const SQL_TOKEN_RE = new RegExp(
  `(--[^\\n]*)|('(?:''|[^'])*')|\\b(${SQL_KEYWORDS.join("|")})\\b|\\b(\\d+)\\b`,
  "gi"
);

/** 把 SQL 逐段塞進 pre，關鍵字/字串/註解/數字各包一個 span，全程用文字節點。 */
export function highlightSql(pre, sql) {
  pre.textContent = "";
  const text = sql || "";
  let last = 0;
  let match;
  SQL_TOKEN_RE.lastIndex = 0;
  while ((match = SQL_TOKEN_RE.exec(text)) !== null) {
    if (match.index > last) pre.appendChild(document.createTextNode(text.slice(last, match.index)));
    const cls = match[1] ? "sql-comment" : match[2] ? "sql-string" : match[3] ? "sql-kw" : "sql-num";
    pre.appendChild(el("span", cls, match[0]));
    last = match.index + match[0].length;
  }
  if (last < text.length) pre.appendChild(document.createTextNode(text.slice(last)));
}
