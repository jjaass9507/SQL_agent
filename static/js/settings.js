const statusEl = document.getElementById('backend-status');
const urlInput = document.getElementById('db-url-input');
const msgEl = document.getElementById('settings-msg');
const saveBtn = document.getElementById('settings-save');
const clearBtn = document.getElementById('settings-clear');

function renderStatus(data) {
  if (data.configured) {
    statusEl.innerHTML =
      '<span class="settings-badge settings-badge-pg">● PostgreSQL</span>' +
      '<span class="settings-mono">' + escHtml(data.masked_url) + '</span>';
  } else {
    statusEl.innerHTML =
      '<span class="settings-badge settings-badge-json">● 本機 JSON</span>' +
      '<span class="settings-mono">data/*.json</span>';
  }
}

function escHtml(s) {
  return (s || '').replace(/[&<>"]/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function showMsg(text, ok) {
  msgEl.textContent = text;
  msgEl.className = 'settings-msg ' + (ok ? 'settings-msg-ok' : 'settings-msg-err');
}

async function loadStatus() {
  try {
    const res = await fetch('/api/settings');
    renderStatus(await res.json());
  } catch (e) {
    statusEl.textContent = '無法載入狀態';
  }
}

async function save(url) {
  saveBtn.disabled = true;
  clearBtn.disabled = true;
  msgEl.className = 'settings-msg hidden';
  try {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ database_url: url }),
    });
    const data = await res.json();
    if (!res.ok) {
      showMsg(data.error || '儲存失敗', false);
    } else {
      renderStatus(data);
      urlInput.value = '';
      showMsg(url ? '已連線，記憶將存入此資料庫。' : '已改回本機儲存。', true);
    }
  } catch (e) {
    showMsg('連線發生錯誤', false);
  } finally {
    saveBtn.disabled = false;
    clearBtn.disabled = false;
  }
}

saveBtn.addEventListener('click', () => {
  const url = urlInput.value.trim();
  if (!url) { showMsg('請先填入連線字串', false); return; }
  save(url);
});

clearBtn.addEventListener('click', () => {
  if (confirm('清除後將改用本機 JSON 儲存，資料庫中既有的專案不會被刪除，但平台會停止讀取它。確定？')) {
    save('');
  }
});

loadStatus();
