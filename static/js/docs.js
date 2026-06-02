// Guard against the mermaid CDN failing to load (offline / blocked / CSP):
// an unguarded reference here would throw and abort the entire docs page.
if (typeof mermaid !== 'undefined') {
  mermaid.initialize({ startOnLoad: false, theme: 'default' });
}

const FILE_INFO = {
  '01_specification.md': { icon: '📄', label: '規格書與資料字典', cardId: 'card-spec' },
  '02_er_diagram.md':    { icon: '🗺', label: 'ER 關聯圖',        cardId: 'card-diagram' },
  '03_ddl.sql':          { icon: '💾', label: 'DDL 腳本',          cardId: 'card-ddl' },
  '04_security_plan.md': { icon: '🔒', label: '效能與安全規劃',    cardId: 'card-security' },
  '05_orm_models.py':    { icon: '🧩', label: 'ORM 模型（SQLAlchemy）' },
  '06_migration.py':     { icon: '🔧', label: 'Migration（Alembic）' },
  '07_queries.sql':      { icon: '🔎', label: '常用查詢範例' },
  '08_incremental_migration.sql': { icon: '🔀', label: '增量 Migration（現有DB→設計）' },
  '09_schema.dbml':      { icon: '📐', label: 'DBML（dbdiagram.io）' },
  '10_schema.puml':      { icon: '🌿', label: 'PlantUML ER 圖' },
  '11_json_schema.json': { icon: '📦', label: 'JSON Schema' },
  '12_data_dictionary.csv': { icon: '📊', label: '資料字典（CSV）' },
};

// On-demand extras: kind → output filename (order defines TOC order)
const EXTRA_INFO = [
  { kind: 'orm',       filename: '05_orm_models.py', icon: '🧩', label: 'ORM 模型（SQLAlchemy）' },
  { kind: 'migration', filename: '06_migration.py',  icon: '🔧', label: 'Migration（Alembic）' },
  { kind: 'query',     filename: '07_queries.sql',   icon: '🔎', label: '常用查詢範例' },
  { kind: 'dbml',      filename: '09_schema.dbml',   icon: '📐', label: 'DBML（dbdiagram.io）' },
  { kind: 'plantuml',  filename: '10_schema.puml',   icon: '🌿', label: 'PlantUML ER 圖' },
  { kind: 'jsonschema',filename: '11_json_schema.json', icon: '📦', label: 'JSON Schema' },
  { kind: 'datadict',  filename: '12_data_dictionary.csv', icon: '📊', label: '資料字典（CSV）' },
];
// Incremental migration only makes sense when an existing DB was imported to diff against
if (typeof HAS_EXISTING_DB !== 'undefined' && HAS_EXISTING_DB) {
  EXTRA_INFO.push({ kind: 'incremental', filename: '08_incremental_migration.sql',
                    icon: '🔀', label: '增量 Migration（現有DB→設計）' });
}

const generatingView = document.getElementById('generating-view');
const readingView = document.getElementById('reading-view');
const topbarBadge = document.getElementById('topbar-badge');
const downloadZipBtn = document.getElementById('download-zip-btn');

let currentDoc = '01_specification.md';
let outputs = {};
let genErrors = {};
let pollTimer = null;
let pollFailCount = 0;

function updateCard(filename, status, content, error) {
  const info = FILE_INFO[filename];
  if (!info) return;
  const card = document.getElementById(info.cardId);
  if (!card) return;

  card.className = `gen-card status-${status}`;

  const statusEl = card.querySelector('.gen-card-status-icon');
  const bodyEl = card.querySelector('.gen-card-body');

  const statusIcons = { done: '✓', loading: '⟳', waiting: '○', failed: '✗' };
  statusEl.textContent = statusIcons[status] || '○';
  statusEl.className = `gen-card-status-icon ${status}`;

  if (status === 'done' && content) {
    const preview = content.split('\n').slice(0, 5).join('\n');
    bodyEl.innerHTML = `<div class="gen-card-preview">${escHtml(preview)}</div>`;
  } else if (status === 'loading') {
    bodyEl.innerHTML = `
      <div class="gen-loading-text">⟳ 正在生成 ${info.label}...</div>
      <div class="gen-loading-bar-bg"><div class="gen-loading-bar-fill"></div></div>`;
  } else if (status === 'failed') {
    const errMsg = error ? escHtml(error.slice(0, 80)) : '';
    bodyEl.innerHTML = `<div class="gen-failed-text">✗ 產出失敗${errMsg ? '：' + errMsg : ''}</div><div style="font-size:11px;color:var(--muted);margin-top:4px;">請重新整理頁面後再試</div>`;
  } else {
    bodyEl.innerHTML = `<div class="gen-waiting-text">等待產出...</div>`;
  }

  // Update progress labels
  const labelEl = document.querySelector(`#progress-labels [data-file="${filename}"]`);
  if (labelEl) {
    const colors = { done: 'var(--success)', loading: 'var(--accent)', waiting: 'var(--muted)', failed: 'var(--error)' };
    const icons  = { done: '✓', loading: '⟳', waiting: '○', failed: '✗' };
    labelEl.style.color = colors[status] || 'var(--muted)';
    labelEl.textContent = `${icons[status] || '○'} ${info.label}`;
  }
}

function updateProgressBar(statuses) {
  const doneCount = Object.values(statuses).filter(s => s === 'done').length;
  const totalCount = Object.keys(statuses).length;
  const pct = totalCount > 0 ? Math.round((doneCount / totalCount) * 100) : 0;
  document.getElementById('progress-bar').style.width = pct + '%';
}

async function poll() {
  try {
    const res = await fetch(`/api/sessions/${SESSION_ID}`);
    const session = await res.json();
    const genStatus = session.generation_status || {};
    genErrors = session.generation_errors || {};
    outputs = session.outputs || {};
    pollFailCount = 0;

    Object.entries(genStatus).forEach(([filename, status]) => {
      updateCard(filename, status, outputs[filename], genErrors[filename]);
    });
    updateProgressBar(genStatus);

    if (session.phase === 'done') {
      clearInterval(pollTimer);
      transitionToReading(session);
    }
  } catch (e) {
    console.error('poll error', e);
    pollFailCount++;
    if (pollFailCount >= 3) {
      clearInterval(pollTimer);
      const bar = document.getElementById('progress-bar');
      if (bar) bar.closest('.gen-progress-wrap').innerHTML =
        `<div style="color:var(--error);font-size:13px;padding:8px 0;">⚠ 連線中斷，無法取得進度 <button onclick="location.reload()" class="btn btn-ghost btn-sm" style="margin-left:8px;">重新整理</button></div>`;
    }
  }
}

function transitionToReading(session) {
  topbarBadge.textContent = '✓ 文件產出完成';
  topbarBadge.className = 'badge badge-done';
  downloadZipBtn.style.display = '';
  const cont = document.getElementById('continue-btn');
  if (cont) cont.style.display = '';

  const tocStatus = document.getElementById('toc-status');
  if (tocStatus) {
    const now = new Date();
    tocStatus.textContent = `✓ 全部完成 · ${now.getFullYear()}/${now.getMonth()+1}/${now.getDate()}`;
  }

  generatingView.classList.add('hidden');
  readingView.classList.remove('hidden');

  outputs = session.outputs || {};
  genErrors = session.generation_errors || genErrors;
  renderExtrasToc();
  renderDoc(currentDoc);
  initWorkbench();
}

function renderDoc(filename) {
  currentDoc = filename;
  const info = FILE_INFO[filename];
  const content = outputs[filename] || '';

  document.getElementById('content-title').textContent = `${info.icon} ${info.label}`;

  document.querySelectorAll('.docs-toc-item').forEach(item => {
    item.classList.toggle('active', item.dataset.doc === filename);
  });

  const contentEl = document.getElementById('docs-content');

  if (!content) {
    const errMsg = genErrors[filename];
    contentEl.innerHTML = `<div style="padding:32px;text-align:center;color:var(--muted);">
      <div style="font-size:32px;margin-bottom:10px;">✗</div>
      <div style="font-weight:600;color:var(--error);margin-bottom:6px;">此文件產出失敗</div>
      ${errMsg ? `<div style="font-size:12px;margin-bottom:12px;">${escHtml(errMsg)}</div>` : ''}
      <button class="btn btn-primary btn-sm" id="regen-btn-${escHtml(filename)}"
        onclick="regenerateFile('${escHtml(filename)}')">↻ 重新產出</button>
    </div>`;
    return;
  }

  if (filename === '02_er_diagram.md') {
    const mermaidMatch = content.match(/```mermaid\n([\s\S]*?)```/);
    if (mermaidMatch) {
      // Strip HTML tags to prevent injection; valid Mermaid syntax uses no angle brackets
      const safeSrc = mermaidMatch[1].replace(/<[^>]*>/g, '');
      if (typeof mermaid !== 'undefined') {
        contentEl.innerHTML = `<div class="mermaid">${safeSrc}</div>`;
        mermaid.run({ nodes: contentEl.querySelectorAll('.mermaid') });
      } else {
        // Diagram library unavailable — degrade gracefully to the raw source
        contentEl.innerHTML = `<div class="banner banner-warning">圖表元件無法載入，以下為原始碼</div>`
          + `<pre>${escHtml(safeSrc)}</pre>`;
      }
    } else {
      contentEl.innerHTML = `<pre>${escHtml(content)}</pre>`;
    }
  } else if (filename.endsWith('.sql')) {
    contentEl.innerHTML = `
      <div class="copy-btn-wrap">
        <pre id="ddl-pre">${highlightSQL(content)}</pre>
      </div>`;
  } else if (/\.(py|dbml|puml|json|csv)$/.test(filename)) {
    contentEl.innerHTML = `<pre class="code-block">${escHtml(content)}</pre>`;
  } else {
    contentEl.innerHTML = `<div class="docs-markdown">${renderMarkdown(content)}</div>`;
  }
}

// ── On-demand extras (ORM / migration / queries) ──
function renderExtrasToc() {
  const host = document.getElementById('extras-toc');
  if (!host) return;
  host.innerHTML = EXTRA_INFO.map(ex => {
    const has = !!outputs[ex.filename];
    const loading = (window._extraLoading || {})[ex.kind];
    if (has) {
      return `<div class="docs-toc-item" data-doc="${ex.filename}">
        <span>${ex.icon}</span><span>${ex.label}</span></div>`;
    }
    return `<button class="docs-toc-gen-btn" data-kind="${ex.kind}" ${loading ? 'disabled' : ''}>
      <span>${loading ? '⟳' : '＋'}</span><span>${loading ? '產生中...' : '產生 ' + ex.label}</span></button>`;
  }).join('');

  host.querySelectorAll('.docs-toc-item').forEach(item => {
    item.addEventListener('click', () => renderDoc(item.dataset.doc));
  });
  host.querySelectorAll('.docs-toc-gen-btn').forEach(btn => {
    btn.addEventListener('click', () => generateExtra(btn.dataset.kind));
  });
}

async function generateExtra(kind) {
  const ex = EXTRA_INFO.find(e => e.kind === kind);
  if (!ex) return;
  window._extraLoading = window._extraLoading || {};
  window._extraLoading[kind] = true;
  renderExtrasToc();
  try {
    const res = await fetch(`/api/sessions/${SESSION_ID}/extras/${kind}/generate`, { method: 'POST' });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || '產生失敗');
    const wait = setInterval(async () => {
      try {
        const s = await (await fetch(`/api/sessions/${SESSION_ID}`)).json();
        const status = (s.generation_status || {})[ex.filename];
        if (status === 'done') {
          clearInterval(wait);
          window._extraLoading[kind] = false;
          outputs = s.outputs || outputs;
          genErrors = s.generation_errors || genErrors;
          renderExtrasToc();
          renderDoc(ex.filename);
        } else if (status === 'failed') {
          clearInterval(wait);
          window._extraLoading[kind] = false;
          genErrors = s.generation_errors || genErrors;
          renderExtrasToc();
          alert('產生失敗：' + (genErrors[ex.filename] || '未知錯誤'));
        }
      } catch { clearInterval(wait); window._extraLoading[kind] = false; renderExtrasToc(); }
    }, 2000);
  } catch (e) {
    window._extraLoading[kind] = false;
    renderExtrasToc();
    alert('⚠ ' + e.message);
  }
}

function highlightSQL(code) {
  return escHtml(code)
    .replace(/\b(CREATE|ALTER|DROP|INSERT|SELECT|UPDATE|DELETE|TABLE|INDEX|REFERENCES|PRIMARY|FOREIGN|KEY|DEFAULT|NOT NULL|NULL|UNIQUE|CHECK|ON DELETE|CASCADE|IF NOT EXISTS|IF EXISTS|EXTENSION)\b/g, '<span class="kw">$1</span>')
    .replace(/\b(UUID|VARCHAR|TEXT|INTEGER|INT|DECIMAL|NUMERIC|BOOLEAN|BOOL|TIMESTAMPTZ|TIMESTAMP|DATE|BIGINT|SMALLINT|SERIAL|BYTEA)\b/g, '<span class="ty">$1</span>')
    .replace(/(--[^\n]*)/g, '<span class="cm">$1</span>');
}

function renderMarkdown(md) {
  return escHtml(md)
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^### (.+)$/gm, '<h3 style="font-size:15px;margin:12px 0 6px;font-weight:700;">$1</h3>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\n\|(.+)\|\n\|[-| ]+\|\n((?:\|.+\|\n)*)/g, buildTable)
    .replace(/\n/g, '<br>');
}

function buildTable(match, headerRow, bodyRows) {
  // headerRow already has outer pipes stripped by the regex capture group
  const headers = headerRow.split('|').map(c => `<th>${c.trim()}</th>`).join('');
  const rows = bodyRows.trim().split('\n').map(row => {
    // Drop only the leading/trailing empties from the outer pipes;
    // keep interior empty cells so columns stay aligned.
    let parts = row.split('|');
    if (parts.length && parts[0].trim() === '') parts = parts.slice(1);
    if (parts.length && parts[parts.length - 1].trim() === '') parts = parts.slice(0, -1);
    const cells = parts.map(c => `<td>${c.trim()}</td>`).join('');
    return `<tr>${cells}</tr>`;
  }).join('');
  return `<table><thead><tr>${headers}</tr></thead><tbody>${rows}</tbody></table>`;
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// HAS_DB is injected by Jinja2 in docs.html
// It is true when the session has a db_url stored server-side

function renderResultTable(data) {
  if (!data.columns || !data.rows || !data.rows.length) {
    return '<div style="padding:8px;color:var(--muted);">（無資料）</div>';
  }
  window._lastQueryResult = data;
  const header = data.columns.map(c => `<th>${escHtml(String(c))}</th>`).join('');
  const body = data.rows.map(r =>
    `<tr>${r.map(cell => `<td>${escHtml(String(cell ?? ''))}</td>`).join('')}</tr>`
  ).join('');
  const note = data.truncated
    ? `<div style="font-size:11px;color:var(--muted);padding:4px 0;">（僅顯示前 ${data.rows.length} 筆）</div>`
    : '';
  const toolbar = `<div class="wb-result-toolbar"><span>${data.row_count} 筆</span>`
    + `<button class="btn btn-ghost btn-sm" onclick="exportQueryCSV()">⬇ CSV</button></div>`;
  return `${toolbar}<div style="overflow:auto;"><table class="workbench-result-table"><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table>${note}</div>`;
}

function exportQueryCSV() {
  const data = window._lastQueryResult;
  if (!data) return;
  const esc = v => {
    const s = String(v ?? '');
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  const lines = [data.columns.map(esc).join(',')];
  for (const row of data.rows) lines.push(row.map(esc).join(','));
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'query_result.csv'; a.click();
  URL.revokeObjectURL(url);
}

function initWorkbench() {
  if (typeof HAS_DB === 'undefined' || !HAS_DB) return;
  if (initWorkbench._done) return;   // idempotent: avoid double event binding
  initWorkbench._done = true;
  const tocItem = document.getElementById('workbench-toc-item');
  if (tocItem) tocItem.style.display = '';

  const runBtn = document.getElementById('wb-run-btn');
  const explainBtn = document.getElementById('wb-explain-btn');
  const listBtn = document.getElementById('wb-list-tables-btn');
  const outEl = document.getElementById('workbench-output');
  const sqlEl = document.getElementById('workbench-sql');

  async function postQuery(sql, endpoint) {
    outEl.innerHTML = '<div class="gen-loading-text">⟳ 執行中...</div>';
    try {
      const res = await fetch(`/api/sessions/${SESSION_ID}/${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sql }),
      });
      const data = await res.json();
      if (data.error) {
        outEl.innerHTML = `<div style="color:var(--error);padding:8px;">${escHtml(data.error)}</div>`;
      } else if (endpoint === 'explain') {
        outEl.innerHTML = `<pre style="font-size:12px;overflow:auto;">${escHtml(data.plan)}</pre>`;
      } else {
        outEl.innerHTML = renderResultTable(data);
        saveHistory(sql);
      }
    } catch (e) {
      outEl.innerHTML = '<div style="color:var(--error);">連線失敗，請重新整理頁面</div>';
    }
  }

  if (runBtn) runBtn.addEventListener('click', () => {
    const sql = (sqlEl.value || '').trim();
    if (sql) postQuery(sql, 'query');
  });

  if (explainBtn) explainBtn.addEventListener('click', () => {
    const sql = (sqlEl.value || '').trim();
    if (sql) postQuery(sql, 'explain');
  });

  // ── Natural language → SQL ────────────────────────────────────────────
  const nlInput = document.getElementById('wb-nl-input');
  const nlBtn = document.getElementById('wb-nl-btn');
  const nlAutorun = document.getElementById('wb-nl-autorun');
  async function generateFromNL() {
    const question = (nlInput.value || '').trim();
    if (!question) return;
    nlBtn.disabled = true;
    const original = nlBtn.textContent;
    nlBtn.textContent = '⟳ 產生中…';
    try {
      const res = await fetch(`/api/sessions/${SESSION_ID}/nl2sql`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
      });
      const data = await res.json();
      if (data.error) {
        outEl.innerHTML = `<div style="color:var(--error);padding:8px;">${escHtml(data.error)}</div>`;
        return;
      }
      sqlEl.value = data.sql;
      sqlEl.focus();
      if (nlAutorun && nlAutorun.checked) postQuery(data.sql, 'query');
    } catch (e) {
      outEl.innerHTML = '<div style="color:var(--error);">連線失敗，請重新整理頁面</div>';
    } finally {
      nlBtn.disabled = false;
      nlBtn.textContent = original;
    }
  }
  if (nlBtn) nlBtn.addEventListener('click', generateFromNL);
  if (nlInput) nlInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); generateFromNL(); }
  });

  if (listBtn) listBtn.addEventListener('click', () => {
    postQuery(
      "SELECT table_name, pg_size_pretty(pg_total_relation_size(quote_ident(table_name))) AS size FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name",
      'query'
    );
  });

  // Keyboard shortcuts: Ctrl+Enter run, Ctrl+Shift+E explain
  if (sqlEl) sqlEl.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      const sql = sqlEl.value.trim();
      if (sql) postQuery(sql, 'query');
    } else if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'e') {
      e.preventDefault();
      const sql = sqlEl.value.trim();
      if (sql) postQuery(sql, 'explain');
    }
  });

  // ── Query history (localStorage) ──────────────────────────────────────
  const HIST_KEY = `wb-history-${SESSION_ID}`;
  function loadHistory() { try { return JSON.parse(localStorage.getItem(HIST_KEY) || '[]'); } catch { return []; } }
  function saveHistory(sql) {
    let h = loadHistory().filter(s => s !== sql);
    h.unshift(sql);
    if (h.length > 20) h = h.slice(0, 20);
    localStorage.setItem(HIST_KEY, JSON.stringify(h));
    renderHistory();
  }
  function renderHistory() {
    const list = document.getElementById('wb-history-list');
    if (!list) return;
    const h = loadHistory();
    list.innerHTML = h.length
      ? h.map((sql, i) => `<div class="wb-history-item" data-idx="${i}" title="${escHtml(sql)}">${escHtml(sql.slice(0, 90))}${sql.length > 90 ? '…' : ''}</div>`).join('')
      : '<div style="padding:8px;color:var(--muted);font-size:12px;">無記錄</div>';
    list.querySelectorAll('.wb-history-item').forEach(el => {
      el.addEventListener('click', () => {
        sqlEl.value = loadHistory()[parseInt(el.dataset.idx)];
        document.getElementById('wb-history-panel').style.display = 'none';
        sqlEl.focus();
      });
    });
  }
  const histBtn = document.getElementById('wb-history-btn');
  if (histBtn) histBtn.addEventListener('click', () => {
    const p = document.getElementById('wb-history-panel');
    p.style.display = p.style.display === 'none' ? '' : 'none';
    if (p.style.display !== 'none') renderHistory();
  });
  const clearHistBtn = document.getElementById('wb-clear-history-btn');
  if (clearHistBtn) clearHistBtn.addEventListener('click', () => { localStorage.removeItem(HIST_KEY); renderHistory(); });

  // ── Schema browser ────────────────────────────────────────────────────
  async function loadSchemaTree() {
    const tree = document.getElementById('workbench-schema-tree');
    if (!tree) return;
    tree.innerHTML = '<div class="workbench-sidebar-loading">載入中…</div>';
    try {
      const res = await fetch(`/api/sessions/${SESSION_ID}/schema-tree`);
      const data = await res.json();
      if (!data.tables || !data.tables.length) {
        tree.innerHTML = '<div class="workbench-sidebar-loading">無結構資料</div>';
        return;
      }
      tree.innerHTML = data.tables.map(t => `
        <div class="wb-tree-table">
          <div class="wb-tree-table-name" data-table="${escHtml(t.name)}">
            <span class="wb-tree-icon">▶</span>${escHtml(t.name)}
          </div>
          <div class="wb-tree-cols" style="display:none;">
            ${(t.columns || []).map(col => `
              <div class="wb-tree-col" data-table="${escHtml(t.name)}" data-col="${escHtml(col.name)}"
                   title="${escHtml(col.type)}${col.is_pk ? ' · PK' : ''}${col.is_fk ? ' · FK→' + escHtml(col.fk_table || '') : ''}">
                <span class="wb-tree-col-key">${col.is_pk ? '🔑' : col.is_fk ? '🔗' : ''}</span>
                <span class="wb-tree-col-name">${escHtml(col.name)}</span>
                <span class="wb-tree-col-type">${escHtml((col.type || '').replace('character varying', 'varchar'))}</span>
              </div>`).join('')}
          </div>
        </div>`).join('');
      tree.querySelectorAll('.wb-tree-table-name').forEach(el => {
        el.addEventListener('click', () => {
          const cols = el.nextElementSibling;
          const open = cols.style.display !== 'none';
          cols.style.display = open ? 'none' : '';
          el.querySelector('.wb-tree-icon').textContent = open ? '▶' : '▼';
          el.classList.toggle('active', !open);
        });
        el.addEventListener('dblclick', () => {
          sqlEl.value = `SELECT *\nFROM ${el.dataset.table}\nLIMIT 100;`;
          sqlEl.focus();
        });
      });
      tree.querySelectorAll('.wb-tree-col').forEach(el => {
        el.addEventListener('click', () => {
          const ref = el.dataset.col;
          const pos = sqlEl.selectionStart, val = sqlEl.value;
          sqlEl.value = val.slice(0, pos) + ref + val.slice(sqlEl.selectionEnd);
          sqlEl.setSelectionRange(pos + ref.length, pos + ref.length);
          sqlEl.focus();
        });
      });
    } catch (e) {
      tree.innerHTML = '<div class="workbench-sidebar-loading" style="color:var(--error);">載入失敗</div>';
    }
  }
  loadSchemaTree();
  const refreshBtn = document.getElementById('wb-refresh-schema');
  if (refreshBtn) refreshBtn.addEventListener('click', loadSchemaTree);

  // TOC click handler for workbench
  const wbTocItem = document.getElementById('workbench-toc-item');
  if (wbTocItem) {
    wbTocItem.addEventListener('click', () => {
      document.querySelectorAll('.docs-toc-item').forEach(el => el.classList.remove('active'));
      wbTocItem.classList.add('active');
      document.getElementById('docs-content').closest('.docs-body').style.display = 'none';
      document.getElementById('workbench-panel').style.display = '';
      document.getElementById('content-title').textContent = '⚙ SQL 工作台';
    });
  }
}

// TOC clicks
document.querySelectorAll('.docs-toc-item').forEach(item => {
  item.addEventListener('click', () => {
    if (item.dataset.doc === '__workbench__') return; // handled by initWorkbench
    document.getElementById('workbench-panel').style.display = 'none';
    document.getElementById('docs-content').closest('.docs-body').style.display = '';
    renderDoc(item.dataset.doc);
  });
});

// Continue iterating: re-open the design for further chat
const continueBtn = document.getElementById('continue-btn');
if (continueBtn) {
  continueBtn.addEventListener('click', async () => {
    continueBtn.disabled = true;
    continueBtn.textContent = '⟳ 開啟中...';
    try {
      const res = await fetch(`/api/sessions/${SESSION_ID}/continue`, { method: 'POST' });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || '失敗');
      window.location.href = `/sessions/${SESSION_ID}/chat`;
    } catch (e) {
      continueBtn.disabled = false;
      continueBtn.textContent = '✏ 繼續修改設計';
      alert('⚠ ' + e.message);
    }
  });
}

function _copyText(text) {
  const btn = document.getElementById('copy-btn');
  function onSuccess() { btn.textContent = '✓ 已複製'; setTimeout(() => { btn.textContent = '📋 複製'; }, 1500); }
  function onFail() { btn.textContent = '⚠ 複製失敗'; setTimeout(() => { btn.textContent = '📋 複製'; }, 2000); }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(onSuccess).catch(() => _execCopy(text, onSuccess, onFail));
  } else {
    _execCopy(text, onSuccess, onFail);
  }
}

function _execCopy(text, onSuccess, onFail) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0;';
  document.body.appendChild(ta);
  ta.focus(); ta.select();
  try { document.execCommand('copy') ? onSuccess() : onFail(); } catch { onFail(); }
  document.body.removeChild(ta);
}

// Copy button
document.getElementById('copy-btn').addEventListener('click', () => {
  _copyText(outputs[currentDoc] || '');
});

// Download single file
document.getElementById('download-btn').addEventListener('click', () => {
  const content = outputs[currentDoc] || '';
  if (!content) return;
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = currentDoc;
  a.click();
  URL.revokeObjectURL(a.href);
});

async function regenerateFile(filename) {
  const btn = document.getElementById(`regen-btn-${filename}`);
  if (btn) { btn.disabled = true; btn.textContent = '⟳ 產出中...'; }
  try {
    const res = await fetch(`/api/sessions/${SESSION_ID}/outputs/${encodeURIComponent(filename)}/regenerate`, { method: 'POST' });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || '失敗');
    // Poll until the specific file is done
    const waitDone = setInterval(async () => {
      try {
        const r = await fetch(`/api/sessions/${SESSION_ID}`);
        const s = await r.json();
        const status = (s.generation_status || {})[filename];
        if (status === 'done') {
          clearInterval(waitDone);
          outputs = s.outputs || {};
          genErrors = s.generation_errors || {};
          renderDoc(filename);
        } else if (status === 'failed') {
          clearInterval(waitDone);
          genErrors = s.generation_errors || {};
          renderDoc(filename);
        }
      } catch { clearInterval(waitDone); }
    }, 2000);
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = '↻ 重新產出'; }
    const contentEl = document.getElementById('docs-content');
    if (contentEl) {
      const errNote = document.createElement('div');
      errNote.style.cssText = 'color:var(--error);font-size:12px;margin-top:8px;';
      errNote.textContent = '⚠ ' + e.message;
      contentEl.appendChild(errNote);
    }
  }
}

// Initialise from server-side data (no need to wait for first poll)
genErrors = INITIAL_GEN_ERRORS || {};

if (SESSION_PHASE === 'done') {
  outputs = INITIAL_OUTPUTS;
  transitionToReading({ outputs: INITIAL_OUTPUTS, generation_errors: INITIAL_GEN_ERRORS });
  initWorkbench();
} else {
  // Apply any already-known status immediately
  if (INITIAL_GEN_STATUS) {
    Object.entries(INITIAL_GEN_STATUS).forEach(([filename, status]) => {
      updateCard(filename, status, (INITIAL_OUTPUTS || {})[filename], (INITIAL_GEN_ERRORS || {})[filename]);
    });
    updateProgressBar(INITIAL_GEN_STATUS);
  }
  pollTimer = setInterval(poll, 2000);
  poll();
}
