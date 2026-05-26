let currentFilter = 'all';
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
  const filtered = currentFilter === 'all'
    ? allSessions
    : allSessions.filter(s => phaseToFilter(s.phase) === currentFilter);

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
  if (phase === 'collecting') return 'inprogress';
  if (phase === 'confirming') return 'confirming';
  if (phase === 'generating') return 'inprogress';
  return 'done';
}

function filterLabel(f) {
  return { inprogress: '進行中', confirming: '待確認', done: '已完成' }[f] || f;
}

function phaseLabel(phase) {
  return { collecting: '進行中', confirming: '待確認', generating: '產出中', done: '已完成' }[phase] || phase;
}

function phaseBadgeClass(phase) {
  if (phase === 'collecting' || phase === 'generating') return 'badge-inprogress';
  if (phase === 'confirming') return 'badge-confirming';
  return 'badge-done';
}

function sessionHref(s) {
  if (s.phase === 'collecting') return `/sessions/${s.id}/chat`;
  if (s.phase === 'confirming') return `/sessions/${s.id}/confirm`;
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
  const isDone = s.phase === 'done';
  return `
    <div class="project-card" onclick="location.href='${href}'">
      <div class="card-header">
        <div class="card-title">📁 ${escHtml(s.title)}</div>
        <span class="badge ${badge}">${label}</span>
      </div>
      <div class="card-meta">
        <span>📅 ${formatDate(s.created_at)}</span>
        ${s.table_count ? `<span>⊟ ${s.table_count} 張表</span>` : ''}
      </div>
      <div class="card-actions">
        <a href="${href}" class="btn btn-primary btn-sm">${isDone ? '查看文件' : '繼續'}</a>
        ${isDone ? '' : `<a href="${href}" class="btn btn-ghost btn-sm">繼續</a>`}
      </div>
    </div>`;
}

function updateResumeBanner() {
  const inprog = allSessions.find(s => s.phase === 'collecting');
  const banner = document.getElementById('resume-banner');
  if (!inprog) { banner.classList.add('hidden'); return; }
  document.getElementById('resume-banner-text').innerHTML =
    `⟳ <strong>${escHtml(inprog.title)}</strong> 進行中 — 需求收集對話待完成`;
  document.getElementById('resume-banner-link').href = `/sessions/${inprog.id}/chat`;
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

function openModal() {
  modal.classList.remove('hidden');
  titleInput.value = '';
  titleInput.focus();
}

const dbToggle = document.getElementById('db-import-toggle');
const dbSection = document.getElementById('db-import-section');
const dbUrlInput = document.getElementById('db-url');
const dbSchemaInput = document.getElementById('db-schema');
const dbStatus = document.getElementById('db-import-status');

dbToggle.addEventListener('change', () => {
  dbSection.style.display = dbToggle.checked ? 'block' : 'none';
});

document.getElementById('new-session-btn').addEventListener('click', e => { e.preventDefault(); openModal(); });
document.getElementById('new-session-btn2').addEventListener('click', openModal);
document.getElementById('modal-cancel').addEventListener('click', () => modal.classList.add('hidden'));
modal.addEventListener('click', e => { if (e.target === modal) modal.classList.add('hidden'); });

document.getElementById('modal-confirm').addEventListener('click', createSession);
titleInput.addEventListener('keydown', e => { if (e.key === 'Enter') createSession(); });

async function createSession() {
  const title = titleInput.value.trim() || '未命名設計';
  const useDb = dbToggle.checked;
  const dbUrl = dbUrlInput ? dbUrlInput.value.trim() : '';
  const dbSchema = (dbSchemaInput && dbSchemaInput.value.trim()) || 'public';
  const confirmBtn = document.getElementById('modal-confirm');

  confirmBtn.disabled = true;
  confirmBtn.textContent = useDb && dbUrl ? '連線中...' : '建立中...';
  if (dbStatus) dbStatus.textContent = '';

  const payload = { title };
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
      confirmBtn.textContent = '開始設計 →';
      return;
    }
    if (session.db_imported) {
      // Brief success flash before redirect
      if (dbStatus) dbStatus.innerHTML = `<span style="color:var(--success);">✓ 已匯入 ${session.db_imported} 張資料表</span>`;
      await new Promise(r => setTimeout(r, 600));
    }
    window.location.href = `/sessions/${session.id}/chat`;
  } catch (e) {
    confirmBtn.disabled = false;
    confirmBtn.textContent = '開始設計 →';
    alert('建立失敗：' + e.message);
  }
}

loadSessions();
