let currentFilter = 'all';
let currentSearch = '';
let allSessions = [];

async function loadSessions() {
  try {
    const res = await fetch('/api/sessions');
    allSessions = await res.json();
    renderSessions();
    updateResumeBanner();
  } catch (e) {
    document.getElementById('sessions-grid').innerHTML =
      '<div style="grid-column:1/-1;text-align:center;padding:40px;color:var(--muted);">載入失敗，請重新整理</div>';
  }
}

function renderSessions() {
  const grid = document.getElementById('sessions-grid');
  let filtered = currentFilter === 'all'
    ? allSessions
    : allSessions.filter(s => phaseToFilter(s.phase) === currentFilter);
  if (currentSearch) {
    const q = currentSearch.toLowerCase();
    filtered = filtered.filter(s => s.title.toLowerCase().includes(q));
  }

  if (filtered.length === 0) {
    const emptyMsg = currentFilter === 'all'
      ? '尚無設計紀錄，點擊「＋ 新建設計」開始'
      : `目前沒有「${filterLabel(currentFilter)}」的專案`;
    grid.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:40px;color:var(--muted);font-size:15px;">${emptyMsg}</div>`;
    return;
  }

  grid.innerHTML = filtered.map(s => buildCard(s)).join('');
}

function phaseToFilter(phase) {
  if (phase === 'collecting' || phase === 'reviewing') return 'inprogress';
  if (phase === 'confirming') return 'confirming';
  if (phase === 'generating') return 'inprogress';
  return 'done';
}

function filterLabel(f) {
  return { inprogress: '進行中', confirming: '待確認', done: '已完成' }[f] || f;
}

function phaseLabel(phase) {
  return {
    collecting: '進行中', confirming: '待確認', generating: '產出中', done: '已完成',
    reviewing: '審查中', review_done: '審查完成',
  }[phase] || phase;
}

function phaseBadgeClass(phase) {
  if (phase === 'collecting' || phase === 'generating' || phase === 'reviewing') return 'badge-inprogress';
  if (phase === 'confirming') return 'badge-confirming';
  return 'badge-done';
}

function sessionHref(s) {
  if (s.phase === 'collecting') return `/sessions/${s.id}/chat`;
  if (s.phase === 'confirming') return `/sessions/${s.id}/confirm`;
  if (s.phase === 'reviewing' || s.phase === 'review_done') return `/sessions/${s.id}/review`;
  return `/sessions/${s.id}/docs`;
}

function formatDate(iso) {
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${String(d.getDate()).padStart(2, '0')}`;
}

function buildCard(s) {
  const href = sessionHref(s);
  const badge = phaseBadgeClass(s.phase);
  const label = phaseLabel(s.phase);
  const isReview = s.mode === 'review';
  const isDone = s.phase === 'done' || s.phase === 'review_done';
  const icon = isReview ? '🔍' : '📁';
  const actionLabel = isDone ? (isReview ? '查看報告' : '查看文件') : '繼續';
  return `
    <div class="project-card" onclick="location.href='${href}'">
      <div class="card-header">
        <div class="card-title">${icon} ${escHtml(s.title)}</div>
        <span class="badge ${badge}">${label}</span>
      </div>
      <div class="card-meta">
        <span>📅 ${formatDate(s.created_at)}</span>
        ${s.table_count ? `<span>⊟ ${s.table_count} 張表</span>` : ''}
      </div>
      <div class="card-actions">
        <a href="${href}" class="btn btn-primary btn-sm">${actionLabel}</a>
        <button class="btn btn-ghost btn-sm" onclick="handleRenameClick(event,'${s.id}')" title="重新命名">✏</button>
        <button class="btn btn-ghost btn-sm" onclick="handleDeleteClick(event,'${s.id}')">🗑</button>
      </div>
    </div>`;
}

// Two-click delete: first click arms, second click within 3s confirms
const _deletePending = {};

function handleDeleteClick(e, sessionId) {
  e.stopPropagation();
  const btn = e.currentTarget;
  if (_deletePending[sessionId]) {
    clearTimeout(_deletePending[sessionId]);
    delete _deletePending[sessionId];
    executeDelete(sessionId, btn);
  } else {
    btn.textContent = '確認刪除？';
    btn.className = 'btn btn-danger btn-sm';
    _deletePending[sessionId] = setTimeout(() => {
      delete _deletePending[sessionId];
      btn.textContent = '🗑';
      btn.className = 'btn btn-ghost btn-sm';
    }, 3000);
  }
}

async function executeDelete(sessionId, btn) {
  btn.disabled = true;
  btn.textContent = '刪除中...';
  try {
    const res = await fetch(`/api/sessions/${sessionId}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('delete failed');
    allSessions = allSessions.filter(s => s.id !== sessionId);
    renderSessions();
    updateResumeBanner();
  } catch {
    btn.disabled = false;
    btn.textContent = '刪除失敗';
    btn.className = 'btn btn-danger btn-sm';
    setTimeout(() => {
      btn.textContent = '🗑';
      btn.className = 'btn btn-ghost btn-sm';
    }, 2000);
  }
}

function handleRenameClick(e, sessionId) {
  e.stopPropagation();
  const card = e.currentTarget.closest('.project-card');
  if (!card) return;
  const titleEl = card.querySelector('.card-title');
  if (!titleEl || titleEl.dataset.editing) return;

  const session = allSessions.find(s => s.id === sessionId);
  if (!session) return;

  titleEl.dataset.editing = '1';
  const icon = session.mode === 'review' ? '🔍' : '📁';
  const origTitle = session.title;

  titleEl.innerHTML = `<input class="card-title-input" value="${escHtml(origTitle)}" maxlength="200" />`;
  const input = titleEl.querySelector('input');
  input.focus();
  input.select();

  let done = false;

  async function save() {
    if (done) return;
    done = true;
    const newTitle = input.value.trim();
    if (!newTitle || newTitle === origTitle) {
      titleEl.innerHTML = `${icon} ${escHtml(origTitle)}`;
      delete titleEl.dataset.editing;
      return;
    }
    input.disabled = true;
    try {
      const res = await fetch(`/api/sessions/${sessionId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: newTitle }),
      });
      if (!res.ok) throw new Error();
      const idx = allSessions.findIndex(s => s.id === sessionId);
      if (idx !== -1) allSessions[idx].title = newTitle;
      renderSessions();
      updateResumeBanner();
    } catch {
      titleEl.innerHTML = `${icon} ${escHtml(origTitle)}`;
      delete titleEl.dataset.editing;
    }
  }

  input.addEventListener('keydown', ev => {
    if (ev.key === 'Enter') { ev.preventDefault(); save(); }
    if (ev.key === 'Escape') { ev.preventDefault(); done = true; titleEl.innerHTML = `${icon} ${escHtml(origTitle)}`; delete titleEl.dataset.editing; }
  });
  input.addEventListener('blur', () => setTimeout(save, 150));
}

function updateResumeBanner() {
  const inprogList = allSessions.filter(s => s.phase === 'collecting' || s.phase === 'reviewing');
  const banner = document.getElementById('resume-banner');
  const linkEl = document.getElementById('resume-banner-link');
  if (!inprogList.length) { banner.classList.add('hidden'); return; }
  if (inprogList.length === 1) {
    const s = inprogList[0];
    const isReview = s.phase === 'reviewing';
    document.getElementById('resume-banner-text').innerHTML =
      `⟳ <strong>${escHtml(s.title)}</strong> ${isReview ? '審查中' : '進行中 — 需求收集待完成'}`;
    linkEl.href = sessionHref(s);
    linkEl.textContent = isReview ? '繼續審查 →' : '繼續對話 →';
  } else {
    document.getElementById('resume-banner-text').innerHTML =
      `⟳ 有 <strong>${inprogList.length}</strong> 個進行中的專案`;
    linkEl.href = '#';
    linkEl.textContent = '查看全部';
    linkEl.onclick = e => { e.preventDefault(); currentFilter = 'inprogress'; document.querySelectorAll('.chip').forEach(c => c.classList.toggle('active', c.dataset.filter === 'inprogress')); renderSessions(); };
  }
  banner.classList.remove('hidden');
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// Filter chips
document.querySelectorAll('.chip').forEach(chip => {
  chip.addEventListener('click', () => {
    document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
    chip.classList.add('active');
    currentFilter = chip.dataset.filter;
    renderSessions();
  });
});

// New session modal
const modal = document.getElementById('new-session-modal');
const titleInput = document.getElementById('new-session-title');
const dbToggle = document.getElementById('db-import-toggle');
const dbToggleRow = document.getElementById('db-import-toggle-row');
const dbSection = document.getElementById('db-import-section');
const dbUrlInput = document.getElementById('db-url');
const dbSchemaInput = document.getElementById('db-schema');
const dbStatus = document.getElementById('db-import-status');
const dbRequiredMark = document.getElementById('db-required-mark');
const confirmBtn = document.getElementById('modal-confirm');

let currentMode = 'design';

function openModal() {
  modal.classList.remove('hidden');
  titleInput.value = '';
  setMode('design');
  titleInput.focus();
}

const ddlSection = document.getElementById('ddl-import-section');
const ddlTextarea = document.getElementById('ddl-textarea');
const ddlStatus = document.getElementById('ddl-import-status');

function setMode(mode) {
  currentMode = mode;
  document.querySelectorAll('.mode-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.mode === mode);
  });
  // reset all conditional sections
  if (ddlSection) ddlSection.style.display = 'none';
  if (mode === 'review') {
    dbToggle.checked = true;
    dbSection.style.display = 'block';
    dbToggleRow.style.display = 'none';
    dbRequiredMark.style.display = '';
    confirmBtn.textContent = '開始審查 →';
    titleInput.placeholder = '例如：現有訂單系統審查';
  } else if (mode === 'ddl') {
    dbToggle.checked = false;
    dbSection.style.display = 'none';
    dbToggleRow.style.display = 'none';
    if (ddlSection) ddlSection.style.display = 'block';
    confirmBtn.textContent = '解析並確認 →';
    titleInput.placeholder = '例如：既有訂單系統 DDL';
  } else {
    dbToggle.checked = false;
    dbSection.style.display = 'none';
    dbToggleRow.style.display = '';
    dbRequiredMark.style.display = 'none';
    confirmBtn.textContent = '開始設計 →';
    titleInput.placeholder = '例如：訂單系統設計';
  }
}

document.getElementById('mode-selector').addEventListener('click', e => {
  const btn = e.target.closest('.mode-btn');
  if (btn) setMode(btn.dataset.mode);
});

dbToggle.addEventListener('change', () => {
  dbSection.style.display = dbToggle.checked ? 'block' : 'none';
});

document.getElementById('new-session-btn').addEventListener('click', e => { e.preventDefault(); openModal(); });
document.getElementById('new-session-btn2').addEventListener('click', openModal);
document.getElementById('modal-cancel').addEventListener('click', () => modal.classList.add('hidden'));
modal.addEventListener('click', e => { if (e.target === modal) modal.classList.add('hidden'); });

confirmBtn.addEventListener('click', createSession);
titleInput.addEventListener('keydown', e => { if (e.key === 'Enter') createSession(); });

async function createSession() {
  // DDL import takes a separate path: parse DDL → land on confirm page
  if (currentMode === 'ddl') {
    const ddl = ddlTextarea ? ddlTextarea.value.trim() : '';
    if (!ddl) {
      if (ddlStatus) ddlStatus.innerHTML = '<span style="color:var(--error);">⚠ 請貼上 CREATE TABLE 語句</span>';
      return;
    }
    confirmBtn.disabled = true;
    confirmBtn.textContent = '解析中...';
    if (ddlStatus) ddlStatus.textContent = '';
    try {
      const res = await fetch('/api/ddl-import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: titleInput.value.trim() || 'DDL 匯入設計', ddl }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'import failed');
      if (ddlStatus) ddlStatus.innerHTML = `<span style="color:var(--success);">✓ 解析出 ${data.table_count} 個資料表</span>`;
      window.location.href = `/sessions/${data.id}/confirm`;
    } catch (e) {
      confirmBtn.disabled = false;
      confirmBtn.textContent = '解析並確認 →';
      if (ddlStatus) ddlStatus.innerHTML = `<span style="color:var(--error);">⚠ ${escHtml(e.message)}</span>`;
    }
    return;
  }

  const title = titleInput.value.trim() || (currentMode === 'review' ? '未命名審查' : '未命名設計');
  const useDb = dbToggle.checked;
  const dbUrl = dbUrlInput ? dbUrlInput.value.trim() : '';
  const dbSchema = (dbSchemaInput && dbSchemaInput.value.trim()) || 'public';

  if (currentMode === 'review' && !dbUrl) {
    if (dbStatus) dbStatus.innerHTML = '<span style="color:var(--error);">⚠ 審查模式需要提供資料庫連線字串</span>';
    dbUrlInput.focus();
    return;
  }

  confirmBtn.disabled = true;
  confirmBtn.textContent = useDb && dbUrl ? '連線中...' : '建立中...';
  if (dbStatus) dbStatus.textContent = '';

  const payload = { title, mode: currentMode };
  if (useDb && dbUrl) {
    payload.db_url = dbUrl;
    payload.db_schema = dbSchema;
  }

  try {
    const res = await fetch('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const session = await res.json();
    if (!res.ok) throw new Error(session.error || 'create failed');

    if (session.db_error) {
      if (dbStatus) dbStatus.innerHTML = `<span style="color:var(--error);">⚠ ${escHtml(session.db_error)}</span>`;
      confirmBtn.disabled = false;
      confirmBtn.textContent = currentMode === 'review' ? '開始審查 →' : '開始設計 →';
      return;
    }
    if (session.db_imported) {
      if (dbStatus) dbStatus.innerHTML = `<span style="color:var(--success);">✓ 已匯入 ${session.db_imported} 張資料表</span>`;
      await new Promise(r => setTimeout(r, 600));
    }
    if (currentMode === 'review') {
      window.location.href = `/sessions/${session.id}/review`;
    } else {
      window.location.href = `/sessions/${session.id}/chat`;
    }
  } catch (e) {
    confirmBtn.disabled = false;
    confirmBtn.textContent = currentMode === 'review' ? '開始審查 →' : '開始設計 →';
    if (dbStatus) dbStatus.innerHTML = `<span style="color:var(--error);">⚠ 建立失敗：${escHtml(e.message)}</span>`;
    else {
      const errEl = document.createElement('div');
      errEl.style.cssText = 'color:var(--error);font-size:13px;margin-top:6px;text-align:center;';
      errEl.textContent = '⚠ 建立失敗：' + e.message;
      document.querySelector('.modal-actions').before(errEl);
      setTimeout(() => errEl.remove(), 4000);
    }
  }
}

// Search
const searchEl = document.getElementById('session-search');
if (searchEl) {
  searchEl.addEventListener('input', () => {
    currentSearch = searchEl.value.trim();
    renderSessions();
  });
}

loadSessions();
