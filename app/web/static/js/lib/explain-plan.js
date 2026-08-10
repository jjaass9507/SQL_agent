// lib/explain-plan.js — 把 PostgreSQL 的 EXPLAIN 文字輸出畫成可讀的樹
//
// `EXPLAIN` 回傳的是一欄純文字，用縮排與 "->" 表示層級，例如：
//
//   HashAggregate  (cost=1189.01..1189.04 rows=3 width=15)
//     Group Key: "通路"
//     ->  Seq Scan on orders  (cost=0.00..944.00 rows=49003 width=11)
//           Filter: (amount > 100)
//
// 這裡把每一行拆成「動作 / 成本 / 預估列數」，並對常見的效能地雷加一句白話說明
// ——DBA 看得懂 Seq Scan，但被問「為什麼慢」的人通常看不懂。

/** 掃全表、巢狀迴圈等值得注意的節點 → 一句人話。 */
const NODE_HINTS = [
  [/\bSeq Scan\b/i, "掃了整張表。資料量大時通常表示缺索引，或條件無法用到現有索引。"],
  [/\bNested Loop\b/i, "逐筆比對兩邊資料。外側筆數一多，成本會急速上升。"],
  [/\bSort\b/i, "需要先把資料排序才能往下走。排序量大時會用到磁碟暫存。"],
  [/\bHash Join\b/i, "先把一邊建成雜湊表再比對，通常比逐筆比對快。"],
  [/\bIndex Scan\b/i, "有走到索引。"],
  [/\bIndex Only Scan\b/i, "只讀索引就取得所需欄位，不必回表，是最理想的情況。"],
];

const COST_RE = /\(cost=([\d.]+)\.\.([\d.]+)\s+rows=(\d+)\s+width=(\d+)\)/;

/**
 * 把 EXPLAIN 的文字列轉成節點陣列。
 * @param {string[]} lines 每一列的 QUERY PLAN 文字
 * @returns {{depth:number, label:string, totalCost:number|null, rows:number|null,
 *            hint:string|null, isDetail:boolean}[]}
 */
export function parsePlan(lines) {
  return lines.map((raw) => {
    const line = String(raw ?? "");
    const indent = line.length - line.trimStart().length;
    let text = line.trim();

    // "->" 是子節點標記，本身不是內容
    const isChild = text.startsWith("->");
    if (isChild) text = text.slice(2).trim();

    // 「Filter: ...」「Group Key: ...」這類是上一個節點的細節，不是獨立節點
    const isDetail = !isChild && /^[A-Z][\w ]*:/.test(text) && !COST_RE.test(text);

    const cost = text.match(COST_RE);
    const label = cost ? text.slice(0, cost.index).trim() : text;

    let hint = null;
    if (!isDetail) {
      const matched = NODE_HINTS.find(([re]) => re.test(label));
      if (matched) hint = matched[1];
    }

    return {
      depth: Math.floor(indent / 2),
      label: label || text,
      totalCost: cost ? Number(cost[2]) : null,
      rows: cost ? Number(cost[3]) : null,
      hint,
      isDetail,
      // 掃全表是最常見的「為什麼慢」答案，單獨標出來
      isWarning: !isDetail && /\bSeq Scan\b/i.test(label),
    };
  });
}

/** 把 QueryResult（columns/rows）的 EXPLAIN 結果畫成樹狀清單。 */
export function renderPlan(container, result) {
  container.textContent = "";
  const lines = (result.rows || []).map((row) => row[0]);
  if (!lines.length) {
    const empty = document.createElement("p");
    empty.className = "form-hint";
    empty.textContent = "沒有取得執行計畫。";
    container.appendChild(empty);
    return;
  }

  const nodes = parsePlan(lines);
  const list = document.createElement("div");
  list.className = "plan-tree";

  for (const node of nodes) {
    const row = document.createElement("div");
    row.className = "plan-node";
    if (node.isDetail) row.classList.add("is-detail");
    if (node.isWarning) row.classList.add("is-warning");
    row.style.paddingLeft = `${node.depth * 16}px`;

    const label = document.createElement("span");
    label.className = "plan-node-label";
    label.textContent = node.label;
    row.appendChild(label);

    if (node.rows !== null) {
      const meta = document.createElement("span");
      meta.className = "plan-node-meta";
      meta.textContent = `預估 ${node.rows.toLocaleString()} 列・成本 ${node.totalCost}`;
      row.appendChild(meta);
    }

    list.appendChild(row);

    if (node.hint) {
      const hint = document.createElement("div");
      hint.className = node.isWarning ? "plan-node-hint is-warning" : "plan-node-hint";
      hint.style.paddingLeft = `${node.depth * 16 + 16}px`;
      hint.textContent = node.hint;
      list.appendChild(hint);
    }
  }

  container.appendChild(list);
}
