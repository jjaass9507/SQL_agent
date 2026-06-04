(function () {
  'use strict';

  const fab = document.getElementById('gda-fab');
  const panel = document.getElementById('gda-panel');
  const closeBtn = document.getElementById('gda-close');
  const messagesEl = document.getElementById('gda-messages');
  const inputEl = document.getElementById('gda-input');
  const sendBtn = document.getElementById('gda-send');
  const ddlConfirm = document.getElementById('gda-ddl-confirm');
  const ddlCode = document.getElementById('gda-ddl-code');
  const ddlRunBtn = document.getElementById('gda-ddl-run');
  const ddlDismissBtn = document.getElementById('gda-ddl-dismiss');
  const ddlResult = document.getElementById('gda-ddl-result');
  const dbSelect = document.getElementById('gda-db-select');

  if (!fab) return;

  function currentDb() {
    return dbSelect ? dbSelect.value : '__all__';
  }

  // ── Panel toggle ──────────────────────────────────────────────────────────

  fab.addEventListener('click', () => {
    panel.classList.add('open');
    panel.setAttribute('aria-hidden', 'false');
    loadDatabases();
    inputEl.focus();
  });
  closeBtn.addEventListener('click', () => {
    panel.classList.remove('open');
    panel.setAttribute('aria-hidden', 'true');
  });

  // ── DB selector ──────────────────────────────────────────────────────────

  let _dbsLoaded = false;
  function loadDatabases() {
    if (_dbsLoaded || !dbSelect) return;
    fetch('/api/db-agent/databases')
      .then(r => r.json())
      .then(dbs => {
        if (!Array.isArray(dbs) || !dbs.length) return;
        _dbsLoaded = true;
        dbSelect.innerHTML = '<option value="__all__">全部</option>' +
          dbs.map(d => `<option value="${escHtml(d.name)}">${escHtml(d.name)}</option>`).join('');
        dbSelect.style.display = dbs.length > 1 ? '' : 'none';
      })
      .catch(() => {});
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  function escHtml(s) {
    return String(s).replace(/[&<>"]/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  function appendBubble(text, type, id) {
    const div = document.createElement('div');
    div.className = `sc-bubble sc-bubble-${type}`;
    if (id) div.id = id;
    div.textContent = text;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return div;
  }

  function removeById(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
  }

  function renderResultTable(data) {
    if (!data || !data.columns) return '';
    const header = data.columns.map(c => `<th>${escHtml(c)}</th>`).join('');
    const body = (data.rows || []).slice(0, 50).map(row =>
      `<tr>${row.map(cell => `<td>${escHtml(cell == null ? '' : String(cell))}</td>`).join('')}</tr>`
    ).join('');
    const note = (data.truncated || (data.rows || []).length > 50)
      ? `<div style="font-size:11px;color:var(--muted);padding:3px 0;">（結果已截斷，前往<a href="/db-agent">完整頁面</a>查看）</div>` : '';
    return `${note}<div style="overflow-x:auto;max-height:200px;overflow-y:auto;"><table class="workbench-result-table"><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  // ── Send message ──────────────────────────────────────────────────────────

  async function sendMessage() {
    const text = (inputEl.value || '').trim();
    if (!text) return;
    inputEl.value = '';
    inputEl.style.height = '';
    hideDdlConfirm();

    appendBubble(text, 'user');
    sendBtn.disabled = true;
    const thinkingId = 'gda-thinking-' + Date.now();
    appendBubble('⟳ 思考中…', 'thinking', thinkingId);

    try {
      const res = await fetch('/api/db-agent/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, db_name: currentDb() }),
      });
      const data = await res.json();
      removeById(thinkingId);

      if (!res.ok) {
        appendBubble('錯誤：' + (data.error || '請求失敗'), 'error');
        return;
      }

      if (data.reply) appendBubble(data.reply, 'ai');

      if (data.query_result) {
        const rowCount = (data.query_result.rows || []).length;
        const wrapper = document.createElement('div');
        wrapper.className = 'sc-bubble sc-bubble-ai';
        wrapper.innerHTML = `查詢完成（${rowCount} 筆）— 前往 <a href="/db-agent" style="color:var(--accent);">完整頁面</a> 查看結果`;
        messagesEl.appendChild(wrapper);
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }
      if (data.query_error) appendBubble('查詢錯誤：' + data.query_error, 'error');
      if (data.ddl_suggestion) showDdlConfirm(data.ddl_suggestion, data.ddl_db);

    } catch (e) {
      removeById(thinkingId);
      appendBubble('連線失敗，請重新整理頁面後再試', 'error');
    } finally {
      sendBtn.disabled = false;
      inputEl.focus();
    }
  }

  // ── DDL confirm ───────────────────────────────────────────────────────────

  function showDdlConfirm(ddl, ddlDb) {
    ddlCode.textContent = ddl;
    ddlResult.textContent = '';
    ddlResult.className = 'schema-chat-ddl-result';
    ddlRunBtn.disabled = false;
    ddlRunBtn.textContent = '執行 DDL';
    ddlRunBtn.style.display = '';
    ddlConfirm.style.display = '';
    ddlConfirm.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    ddlRunBtn.onclick = () => executeDdl(ddl, ddlDb);
  }

  function hideDdlConfirm() { ddlConfirm.style.display = 'none'; }
  ddlDismissBtn.addEventListener('click', hideDdlConfirm);

  async function executeDdl(ddl, ddlDb) {
    ddlRunBtn.disabled = true;
    ddlRunBtn.textContent = '⟳ 執行中…';
    ddlResult.textContent = '';
    ddlResult.className = 'schema-chat-ddl-result';
    try {
      const res = await fetch('/api/db-agent/execute-ddl', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ddl, db_name: ddlDb || currentDb() }),
      });
      const data = await res.json();
      if (data.ok) {
        ddlResult.textContent = `✓ 已執行 ${data.statements_run} 條語句`;
        ddlResult.classList.add('ok');
        ddlRunBtn.style.display = 'none';
        appendBubble(`✓ DDL 執行成功（${data.statements_run} 條語句）`, 'ai');
      } else {
        ddlResult.textContent = '✗ ' + (data.error || '執行失敗');
        ddlResult.classList.add('err');
        ddlRunBtn.disabled = false;
        ddlRunBtn.textContent = '執行 DDL';
      }
    } catch (e) {
      ddlResult.textContent = '✗ 連線失敗';
      ddlResult.classList.add('err');
      ddlRunBtn.disabled = false;
      ddlRunBtn.textContent = '執行 DDL';
    }
  }

  // ── Input events ──────────────────────────────────────────────────────────

  sendBtn.addEventListener('click', sendMessage);

  inputEl.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  inputEl.addEventListener('input', function () {
    this.style.height = '';
    this.style.height = Math.min(this.scrollHeight, 120) + 'px';
  });
})();
