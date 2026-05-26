mermaid.initialize({ startOnLoad: false, theme: 'default' });

const FILE_INFO = {
  '01_specification.md': { icon: '📄', label: '規格書與資料字典', cardId: 'card-spec' },
  '02_er_diagram.md':    { icon: '🗺', label: 'ER 關聯圖',        cardId: 'card-diagram' },
  '03_ddl.sql':          { icon: '💾', label: 'DDL 腳本',          cardId: 'card-ddl' },
  '04_security_plan.md': { icon: '🔒', label: '效能與安全規劃',    cardId: 'card-security' },
};

const generatingView = document.getElementById('generating-view');
const readingView = document.getElementById('reading-view');
const topbarBadge = document.getElementById('topbar-badge');
const downloadZipBtn = document.getElementById('download-zip-btn');

let currentDoc = '01_specification.md';
let outputs = {};
let pollTimer = null;

function updateCard(filename, status, content) {
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
    bodyEl.innerHTML = `<div class="gen-failed-text">✗ 產出失敗</div><button class="btn btn-ghost btn-sm" onclick="retryGeneration()">重試</button>`;
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
    outputs = session.outputs || {};

    Object.entries(genStatus).forEach(([filename, status]) => {
      updateCard(filename, status, outputs[filename]);
    });
    updateProgressBar(genStatus);

    if (session.phase === 'done') {
      clearInterval(pollTimer);
      transitionToReading(session);
    }
  } catch (e) {
    console.error('poll error', e);
  }
}

function transitionToReading(session) {
  topbarBadge.textContent = '✓ 文件產出完成';
  topbarBadge.className = 'badge badge-done';
  downloadZipBtn.style.display = '';

  const tocStatus = document.getElementById('toc-status');
  if (tocStatus) {
    const now = new Date();
    tocStatus.textContent = `✓ 全部完成 · ${now.getFullYear()}/${now.getMonth()+1}/${now.getDate()}`;
  }

  generatingView.classList.add('hidden');
  readingView.classList.remove('hidden');

  outputs = session.outputs || {};
  renderDoc(currentDoc);
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

  if (filename === '02_er_diagram.md') {
    const mermaidMatch = content.match(/```mermaid\n([\s\S]*?)```/);
    if (mermaidMatch) {
      contentEl.innerHTML = `<div class="mermaid">${escHtml(mermaidMatch[1])}</div>`;
      mermaid.run({ nodes: contentEl.querySelectorAll('.mermaid') });
    } else {
      contentEl.innerHTML = `<pre>${escHtml(content)}</pre>`;
    }
  } else if (filename === '03_ddl.sql') {
    contentEl.innerHTML = `
      <div class="copy-btn-wrap">
        <pre id="ddl-pre">${highlightSQL(content)}</pre>
      </div>`;
  } else {
    contentEl.innerHTML = `<div class="docs-markdown">${renderMarkdown(content)}</div>`;
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
    .replace(/^### (.+)$/gm, '<h3 style="font-size:15px;margin:12px 0 6px;font-weight:700;">$3</h3>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\n\|(.+)\|\n\|[-| ]+\|\n((?:\|.+\|\n)*)/g, buildTable)
    .replace(/\n/g, '<br>');
}

function buildTable(match, headerRow, bodyRows) {
  const headers = headerRow.split('|').filter(c => c.trim()).map(c => `<th>${c.trim()}</th>`).join('');
  const rows = bodyRows.trim().split('\n').map(row => {
    const cells = row.split('|').filter(c => c.trim()).map(c => `<td>${c.trim()}</td>`).join('');
    return `<tr>${cells}</tr>`;
  }).join('');
  return `<table><thead><tr>${headers}</tr></thead><tbody>${rows}</tbody></table>`;
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// TOC clicks
document.querySelectorAll('.docs-toc-item').forEach(item => {
  item.addEventListener('click', () => renderDoc(item.dataset.doc));
});

// Copy button
document.getElementById('copy-btn').addEventListener('click', () => {
  const content = outputs[currentDoc] || '';
  navigator.clipboard.writeText(content).then(() => {
    const btn = document.getElementById('copy-btn');
    btn.textContent = '✓ 已複製';
    setTimeout(() => { btn.textContent = '📋 複製'; }, 1500);
  });
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

// Initialise
if (SESSION_PHASE === 'done') {
  // Already done — fetch outputs immediately and show reading view
  fetch(`/api/sessions/${SESSION_ID}`)
    .then(r => r.json())
    .then(session => transitionToReading(session));
} else {
  // Start polling
  pollTimer = setInterval(poll, 2000);
  poll(); // immediate first check
}
