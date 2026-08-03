// lib/ddl-impact.js — 把一段待審 DDL 翻成使用者看得懂的影響說明
//
// 這裡只負責「說明」，不負責「把關」。真正的護欄在後端
// app/rules/sql_safety.py 的 check_ddl_allowlist()，提案時與核准時各跑一次。
// 因此本檔就算分類得不夠精準也不會放行任何東西。

// 語句分類：後端 allowlist 只放行這幾種，所以列舉得完。
const KINDS = [
  [/^\s*CREATE\s+TABLE\b/i, "新增資料表"],
  [/^\s*CREATE\s+(UNIQUE\s+)?INDEX\b/i, "建立索引"],
  [/^\s*ALTER\s+TABLE\s+\S+\s+ADD\s+COLUMN\b/i, "新增欄位"],
  [/^\s*ALTER\s+TABLE\s+\S+\s+ADD\s+CONSTRAINT\b/i, "新增約束"],
  [/^\s*COMMENT\s+ON\b/i, "加上說明註解"],
];

// 只切最外層的 `;`，字串與註解裡的分號不算（對齊後端 split_statements 的語意）
function splitStatements(ddl) {
  const skeleton = (ddl || "")
    .replace(/\/\*[\s\S]*?\*\//g, (m) => " ".repeat(m.length))
    .replace(/--[^\n]*/g, (m) => " ".repeat(m.length))
    .replace(/'(?:''|[^'])*'/g, (m) => " ".repeat(m.length))
    .replace(/"(?:""|[^"])*"/g, (m) => " ".repeat(m.length));
  const parts = [];
  let start = 0;
  for (let i = 0; i < skeleton.length; i += 1) {
    if (skeleton[i] === ";") {
      parts.push(ddl.slice(start, i));
      start = i + 1;
    }
  }
  parts.push(ddl.slice(start));
  return parts.map((p) => p.trim()).filter(Boolean);
}

/**
 * @returns {{summary: string, warnings: string[], statementCount: number}}
 *   summary  白話變更清單，例如「新增 2 張資料表、建立 3 個索引」
 *   warnings dry-run 測不到、但在有資料的正式表上會出事的情況
 */
export function analyzeDdl(ddl) {
  const statements = splitStatements(ddl);
  const counts = new Map();
  const warnings = [];

  for (const statement of statements) {
    const matched = KINDS.find(([re]) => re.test(statement));
    const label = matched ? matched[1] : "其他變更";
    counts.set(label, (counts.get(label) || 0) + 1);

    // dry-run 在空的臨時 schema 裡跑，這兩種情況在那裡一定會過，
    // 但在有資料、有流量的正式表上會出事，必須明講。
    if (/^\s*CREATE\s+(UNIQUE\s+)?INDEX\b/i.test(statement) && !/\bCONCURRENTLY\b/i.test(statement)) {
      warnings.push(
        "建立索引時沒有加 CONCURRENTLY：資料量大的表在建立期間會被鎖住無法寫入。"
      );
    }
    if (/\bADD\s+COLUMN\b/i.test(statement) && /\bNOT\s+NULL\b/i.test(statement)
        && !/\bDEFAULT\b/i.test(statement)) {
      warnings.push(
        "新增的欄位設為 NOT NULL 但沒有預設值：表裡已經有資料時，這句會執行失敗。"
      );
    }
  }

  const summary = [...counts.entries()]
    .map(([label, n]) => `${label} ${n} 項`)
    .join("、");

  return {
    summary: summary || "沒有可辨識的語句",
    warnings: [...new Set(warnings)],
    statementCount: statements.length,
  };
}
