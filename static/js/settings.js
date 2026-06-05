// ── Shared helpers ────────────────────────────────────────────────────────────

function escHtml(s) {
  return (s || '').replace(/[&<>"]/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function showMsg(el, text, ok) {
  el.textContent = text;
  el.className = 'settings-msg ' + (ok ? 'settings-msg-ok' : 'settings-msg-err');
}

// ── Platform DB ───────────────────────────────────────────────────────────────

const statusEl = document.getElementById('backend-status');
const urlInput = document.getElementById('db-url-input');
const platformSchemaInput = document.getElementById('platform-schema-input');
const msgEl = document.getElementById('settings-msg');
const saveBtn = document.getElementById('settings-save');
const clearBtn = document.getElementById('settings-clear');

function renderStatus(data) {
  if (data.configured) {
    statusEl.innerHTML =
      '<span class="settings-badge settings-badge-pg">● PostgreSQL</span>' +
      '<span class="settings-mono">' + escHtml(data.masked_url) + '</span>' +
      (data.platform_schema && data.platform_schema !== 'public'
        ? '<span class="settings-mono" style="margin-left:8px;">schema: ' + escHtml(data.platform_schema) + '</span>'
        : '');
  } else {
    statusEl.innerHTML =
      '<span class="settings-badge settings-badge-json">● 本機 JSON</span>' +
      '<span class="settings-mono">data/*.json</span>';
  }
}

async function loadStatus() {
  try {
    const res = await fetch('/api/settings');
    const data = await res.json();
    renderStatus(data);
    if (data.platform_schema) platformSchemaInput.value = data.platform_schema;
    renderBizList(data.business_databases || []);
  } catch (e) {
    statusEl.textContent = '無法載入狀態';
  }
}

async function savePlatform(url) {
  saveBtn.disabled = true;
  clearBtn.disabled = true;
  msgEl.className = 'settings-msg hidden';
  const platformSchema = (platformSchemaInput.value || '').trim() || 'public';
  try {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ database_url: url, platform_schema: platformSchema }),
    });
    const data = await res.json();
    if (!res.ok) {
      showMsg(msgEl, data.error || '儲存失敗', false);
    } else {
      renderStatus(data);
      urlInput.value = '';
      showMsg(msgEl, url ? '已連線，平台記憶將存入此資料庫。' : '已改回本機儲存。', true);
    }
  } catch (e) {
    showMsg(msgEl, '連線發生錯誤', false);
  } finally {
    saveBtn.disabled = false;
    clearBtn.disabled = false;
  }
}

saveBtn.addEventListener('click', () => {
  const url = urlInput.value.trim();
  if (!url) { showMsg(msgEl, '請先填入連線字串', false); return; }
  savePlatform(url);
});

clearBtn.addEventListener('click', () => {
  if (confirm('清除後將改用本機 JSON 儲存，資料庫中既有的專案不會被刪除，但平台會停止讀取它。確定？')) {
    savePlatform('');
  }
});

// ── Business DBs (multi-named) ────────────────────────────────────────────────

const bizListEl = document.getElementById('biz-db-list');
const bizNameInput = document.getElementById('biz-name-input');
const bizUrlInput = document.getElementById('biz-url-input');
const bizMsgEl = document.getElementById('biz-msg');
const bizAddBtn = document.getElementById('biz-add');

function renderBizList(dbs) {
  if (!dbs || !dbs.length) {
    bizListEl.innerHTML = '<div style="font-size:13px;color:var(--muted);padding:4px 0;">尚未設定任何業務資料庫</div>';
    return;
  }
  bizListEl.innerHTML = dbs.map(db => `
    <div class="settings-db-row" data-name="${escHtml(db.name)}">
      <span class="settings-badge settings-badge-pg">●</span>
      <span style="font-weight:600;min-width:100px;">${escHtml(db.name)}</span>
      <span class="settings-mono" style="flex:1;overflow:hidden;text-overflow:ellipsis;">${escHtml(db.masked_url)}</span>
      <button class="btn btn-ghost btn-sm biz-remove-btn" data-name="${escHtml(db.name)}">移除</button>
    </div>
  `).join('');

  bizListEl.querySelectorAll('.biz-remove-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const name = btn.dataset.name;
      if (!confirm(`移除資料庫「${name}」？助手將不再能互動此資料庫。`)) return;
      btn.disabled = true;
      try {
        const res = await fetch('/api/settings/business-db', {
          method: 'DELETE',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name }),
        });
        const data = await res.json();
        if (!res.ok) {
          alert(data.error || '移除失敗');
          btn.disabled = false;
        } else {
          renderBizList(data.business_databases || []);
        }
      } catch (e) {
        console.error('[settings] remove business DB failed:', e);
        alert('連線發生錯誤');
        btn.disabled = false;
      }
    });
  });
}

bizAddBtn.addEventListener('click', async () => {
  const name = bizNameInput.value.trim();
  const url = bizUrlInput.value.trim();
  bizMsgEl.className = 'settings-msg hidden';
  if (!name) { showMsg(bizMsgEl, '請填入資料庫名稱', false); return; }
  if (!url) { showMsg(bizMsgEl, '請填入連線字串', false); return; }

  bizAddBtn.disabled = true;
  try {
    const res = await fetch('/api/settings/business-db', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, url }),
    });
    const data = await res.json();
    if (!res.ok) {
      showMsg(bizMsgEl, data.error || '新增失敗', false);
    } else {
      renderBizList(data.business_databases || []);
      bizNameInput.value = '';
      bizUrlInput.value = '';
      showMsg(bizMsgEl, `已新增「${name}」，助手正在學習此資料庫的結構。`, true);
    }
  } catch (e) {
    console.error('[settings] add business DB failed:', e);
    showMsg(bizMsgEl, '連線發生錯誤', false);
  } finally {
    bizAddBtn.disabled = false;
  }
});

loadStatus();
