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
        ${isDone ? '' : `<a href="${href}" class="btn btn-ghost btn-sm">繼續</a>`}
      </div>
    </div>`;
}

function updateResumeBanner() {
  const inprog = allSessions.find(s => s.phase === 'collecting' || s.phase === 'reviewing');
  const banner = document.getElementById('resume-banner');
  if (!inprog) { banner.classList.add('hidden'); return; }
  const isReview = inprog.phase === 'reviewing';
  document.getElementById('resume-banner-text').innerHTML =
    `⟳ <strong>${escHtml(inprog.title)}</strong> ${isReview ? '審查中 — AI 正在分析資料庫結構' : '進行中 — 需求收集對話待完成'}`;
  document.getElementById('resume-banner-link').href = sessionHref(inprog);
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

function setMode(mode) {
  currentMode = mode;
  document.querySelectorAll('.mode-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.mode === mode);
  });
  if (mode === 'review') {
    dbToggle.checked = true;
    dbSection.style.display = 'block';
    dbToggleRow.style.display = 'none';
    dbRequiredMark.style.display = '';
    confirmBtn.textContent = '開始審查 →';
    titleInput.placeholder = '例如：現有訂單系統審查';
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

loadSessions();
