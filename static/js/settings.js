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
    renderBizStatus(data);
    if (data.biz_schema) bizSchemaInput.value = data.biz_schema;
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

// ── Business DB ───────────────────────────────────────────────────────────────

const bizStatusEl = document.getElementById('biz-status');
const bizUrlInput = document.getElementById('biz-url-input');
const bizSchemaInput = document.getElementById('biz-schema-input');
const bizMsgEl = document.getElementById('biz-msg');
const bizSaveBtn = document.getElementById('biz-save');
const bizClearBtn = document.getElementById('biz-clear');

function renderBizStatus(data) {
  if (data.biz_configured) {
    bizStatusEl.innerHTML =
      '<span class="settings-badge settings-badge-pg">● 已設定</span>' +
      '<span class="settings-mono">' + escHtml(data.biz_masked_url) + '</span>' +
      (data.biz_schema && data.biz_schema !== 'public'
        ? '<span class="settings-mono" style="margin-left:8px;">schema: ' + escHtml(data.biz_schema) + '</span>'
        : '');
  } else {
    bizStatusEl.innerHTML =
      '<span class="settings-badge settings-badge-json">● 未設定</span>';
  }
}

async function saveBiz(url) {
  bizSaveBtn.disabled = true;
  bizClearBtn.disabled = true;
  bizMsgEl.className = 'settings-msg hidden';
  const schema = (bizSchemaInput.value || '').trim() || 'public';
  try {
    const res = await fetch('/api/settings/business-db', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ database_url: url, schema }),
    });
    const data = await res.json();
    if (!res.ok) {
      showMsg(bizMsgEl, data.error || '儲存失敗', false);
    } else {
      renderBizStatus(data);
      bizUrlInput.value = '';
      showMsg(bizMsgEl, url ? '業務資料庫已設定，助手將開始學習此資料庫。' : '已清除業務資料庫設定。', true);
    }
  } catch (e) {
    showMsg(bizMsgEl, '連線發生錯誤', false);
  } finally {
    bizSaveBtn.disabled = false;
    bizClearBtn.disabled = false;
  }
}

bizSaveBtn.addEventListener('click', () => {
  const url = bizUrlInput.value.trim();
  if (!url) { showMsg(bizMsgEl, '請先填入連線字串', false); return; }
  saveBiz(url);
});

bizClearBtn.addEventListener('click', () => {
  if (confirm('清除業務資料庫設定後，助手將無法互動。確定？')) {
    saveBiz('');
  }
});

loadStatus();
