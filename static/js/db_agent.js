(function () {
  'use strict';

  const messagesEl = document.getElementById('da-messages');
  const inputEl = document.getElementById('da-input');
  const sendBtn = document.getElementById('da-send');
  const schemaTree = document.getElementById('da-schema-tree');
  const clearBtn = document.getElementById('da-clear-chat');
  const checkDocsBtn = document.getElementById('da-check-docs');
  const refreshBtn = document.getElementById('da-refresh-schema');
  const sidebarRefreshBtn = document.getElementById('da-sidebar-refresh');
  const dbSelect = document.getElementById('da-db-select');
  const resultsPanel = document.getElementById('da-results-panel');
  const resultsList = document.getElementById('da-results-list');
  const resultsClearBtn = document.getElementById('da-results-clear');
  const designPanel = document.getElementById('da-design-panel');
  const pendingListEl = document.getElementById('da-pending-list');
  const pendingRefreshBtn = document.getElementById('da-pending-refresh');
  const adminTokenInput = document.getElementById('da-admin-token');

  if (!messagesEl) return; // no-db state, page shows redirect

  // ── Helpers ───────────────────────────────────────────────────────────────

  function escHtml(s) {
    return String(s).replace(/[&<>"]/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  function appendBubble(text, type, id) {
    const div = document.createElement('div');
    div.className = `da-bubble da-bubble-${type}`;
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

  // ── Step trail (collapsible tool-call trace) ────────────────────────────────

  function appendStepTrail(steps) {
    if (!steps || !steps.length) return;
    const wrap = document.createElement('div');
    wrap.className = 'da-steps';

    const toggle = document.createElement('button');
    toggle.className = 'da-steps-toggle';
    toggle.type = 'button';
    toggle.innerHTML = `<span class="da-steps-arrow">▶</span> 已呼叫 ${steps.length} 個工具`;
    wrap.appendChild(toggle);

    const list = document.createElement('div');
    list.className = 'da-steps-list';
    list.innerHTML = steps.map(s => `
      <div class="da-step">
        <span class="da-step-tool">${escHtml(s.tool || '')}</span>
        <span class="da-step-summary">${escHtml(s.result_summary || '')}</span>
      </div>`).join('');
    wrap.appendChild(list);

    toggle.addEventListener('click', () => {
      const open = list.classList.toggle('open');
      toggle.querySelector('.da-steps-arrow').textContent = open ? '▼' : '▶';
    });

    messagesEl.appendChild(wrap);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function renderResultTable(data) {
    if (!data || !data.columns) return '';
    const header = data.columns.map(c => `<th>${escHtml(c)}</th>`).join('');
    const body = (data.rows || []).map(row =>
      `<tr>${row.map(cell => `<td>${escHtml(cell == null ? '' : String(cell))}</td>`).join('')}</tr>`
    ).join('');
    const note = data.truncated ? `<div style="font-size:11px;color:var(--muted);padding:4px 0;">（結果已截斷）</div>` : '';
    return `${note}<div style="overflow-x:auto;"><table class="workbench-result-table"><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  function currentDb() {
    return dbSelect ? dbSelect.value : '__all__';
  }

  // ── DB selector ───────────────────────────────────────────────────────────

  function loadDatabases() {
    if (!dbSelect) return;
    fetch('/api/db-agent/databases')
      .then(r => r.json())
      .then(dbs => {
        if (!Array.isArray(dbs) || !dbs.length) return;
        dbSelect.innerHTML = '<option value="__all__">全部資料庫</option>' +
          dbs.map(d => `<option value="${escHtml(d.name)}">${escHtml(d.name)}</option>`).join('');
        dbSelect.style.display = dbs.length > 1 ? '' : 'none';
      })
      .catch(() => {});
  }

  if (dbSelect) {
    dbSelect.addEventListener('change', loadSchemaTree);
  }

  // ── Schema tree ───────────────────────────────────────────────────────────

  function renderTableList(tables) {
    if (!tables || !tables.length) return '<div class="workbench-sidebar-loading">（無資料表）</div>';
    return tables.map(t => `
      <div class="wb-tree-table">
        <div class="wb-tree-table-name" data-table="${escHtml(t.name)}">
          <span class="wb-tree-icon">▶</span>${escHtml(t.name)}
        </div>
        <div class="wb-tree-cols" style="display:none;">
          ${(t.columns || []).map(col => `
            <div class="wb-tree-col" title="${escHtml(col.type)}${col.is_pk ? ' · PK' : ''}${col.is_fk ? ' · FK→' + (col.fk_table || '') : ''}">
              <span class="wb-tree-col-key">${col.is_pk ? '🔑' : col.is_fk ? '🔗' : ''}</span>
              <span class="wb-tree-col-name">${escHtml(col.name)}</span>
              <span class="wb-tree-col-type">${escHtml(col.type)}</span>
            </div>`).join('')}
        </div>
      </div>`).join('');
  }

  function attachTreeEvents(container) {
    container.querySelectorAll('.wb-tree-table-name').forEach(el => {
      el.addEventListener('click', function () {
        const cols = this.nextElementSibling;
        const icon = this.querySelector('.wb-tree-icon');
        const open = cols.style.display !== 'none';
        cols.style.display = open ? 'none' : '';
        if (icon) icon.textContent = open ? '▶' : '▼';
      });
    });
  }

  function loadSchemaTree() {
    schemaTree.innerHTML = '<div class="workbench-sidebar-loading">載入中…</div>';
    const db = currentDb();
    const url = '/api/db-agent/schema-tree' + (db && db !== '__all__' ? `?db=${encodeURIComponent(db)}` : '');
    fetch(url)
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          schemaTree.innerHTML = `<div class="workbench-sidebar-loading" style="color:var(--error);">${escHtml(data.error)}</div>`;
          return;
        }

        if (data.databases) {
          if (!data.databases.length) {
            schemaTree.innerHTML = '<div class="workbench-sidebar-loading">（無資料庫）</div>';
            return;
          }
          schemaTree.innerHTML = data.databases.map(db => `
            <div class="wb-tree-db-group">
              <div class="wb-tree-db-header">${escHtml(db.name)}</div>
              ${renderTableList(db.tables)}
            </div>`).join('');
        } else {
          const tables = data.tables || [];
          if (!tables.length) {
            schemaTree.innerHTML = '<div class="workbench-sidebar-loading">（無資料表）</div>';
            return;
          }
          schemaTree.innerHTML = renderTableList(tables);
        }
        attachTreeEvents(schemaTree);
      })
      .catch(() => {
        schemaTree.innerHTML = '<div class="workbench-sidebar-loading" style="color:var(--error);">載入失敗</div>';
      });
  }

  loadDatabases();
  loadSchemaTree();
  if (sidebarRefreshBtn) sidebarRefreshBtn.addEventListener('click', loadSchemaTree);
  if (refreshBtn) refreshBtn.addEventListener('click', loadSchemaTree);

  // ── Send message ──────────────────────────────────────────────────────────

  async function sendMessage() {
    const text = (inputEl.value || '').trim();
    if (!text) return;
    inputEl.value = '';
    inputEl.style.height = '';

    appendBubble(text, 'user');
    sendBtn.disabled = true;
    const thinkingId = 'da-thinking-' + Date.now();
    appendBubble('⟳ 思考中…', 'thinking', thinkingId);

    const db = currentDb();
    try {
      const res = await fetch('/api/db-agent/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, db_name: db }),
      });
      const data = await res.json();
      removeById(thinkingId);

      if (!res.ok) {
        appendBubble('錯誤：' + (data.error || '請求失敗'), 'error');
        return;
      }

      if (data.reply) appendBubble(data.reply, 'ai');
      appendStepTrail(data.steps);

      // Query result → separate results panel
      if (data.query_result) {
        const cardId = appendResultCard(data.query_result, data.query_sql, data.query_db);
        const rowCount = (data.query_result.rows || []).length;
        const refBubble = document.createElement('div');
        refBubble.className = 'da-bubble da-bubble-ai';
        refBubble.innerHTML = `查詢完成（${rowCount} 筆）— <a href="#${cardId}" class="da-result-link">↓ 見下方結果</a>`;
        messagesEl.appendChild(refBubble);
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }
      if (data.query_error) {
        appendBubble('查詢錯誤：' + data.query_error, 'error');
      }

      if (data.ddl_suggestion) {
        appendDdlBlock(data.ddl_suggestion, data.ddl_db);
      }

      if (data.design_session) {
        openDesignPanel(data.design_session);
      }

      if (data.proposal) {
        loadPendingRequests();
      }
    } catch (e) {
      removeById(thinkingId);
      appendBubble('連線失敗，請重新整理頁面後再試', 'error');
    } finally {
      sendBtn.disabled = false;
      inputEl.focus();
    }
  }

  // ── DDL block ─────────────────────────────────────────────────────────────

  function appendDdlBlock(ddl, ddlDb) {
    const block = document.createElement('div');
    block.className = 'da-ddl-block';

    const label = document.createElement('div');
    label.className = 'da-ddl-label';
    label.textContent = 'AI 建議的 DDL（送審後由管理員核准才會執行）：' + (ddlDb ? ` [目標：${ddlDb}]` : '');
    block.appendChild(label);

    const pre = document.createElement('pre');
    pre.className = 'da-ddl-pre';
    pre.textContent = ddl;
    block.appendChild(pre);

    const actions = document.createElement('div');
    actions.className = 'da-ddl-actions';
    const submitBtn = document.createElement('button');
    submitBtn.className = 'btn btn-primary btn-sm';
    submitBtn.textContent = '送審';
    const dismissBtn = document.createElement('button');
    dismissBtn.className = 'btn btn-ghost btn-sm';
    dismissBtn.textContent = '取消';
    const resultEl = document.createElement('div');
    resultEl.className = 'da-ddl-result';
    actions.appendChild(submitBtn);
    actions.appendChild(dismissBtn);
    block.appendChild(actions);
    block.appendChild(resultEl);

    messagesEl.appendChild(block);
    messagesEl.scrollTop = messagesEl.scrollHeight;

    submitBtn.addEventListener('click', async () => {
      submitBtn.disabled = true;
      submitBtn.textContent = '⟳ 送審中…';
      resultEl.textContent = '';
      resultEl.className = 'da-ddl-result';
      try {
        const res = await fetch('/api/change-requests', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ddl, db_name: ddlDb || currentDb() }),
        });
        const data = await res.json();
        if (res.ok) {
          resultEl.textContent = `✓ 已送審（編號 ${data.id}），等待管理員核准`;
          resultEl.classList.add('ok');
          submitBtn.style.display = 'none';
          loadPendingRequests();
        } else {
          resultEl.textContent = '✗ ' + (data.error || '送審失敗');
          resultEl.classList.add('err');
          submitBtn.disabled = false;
          submitBtn.textContent = '送審';
        }
      } catch (e) {
        resultEl.textContent = '✗ 連線失敗';
        resultEl.classList.add('err');
        submitBtn.disabled = false;
        submitBtn.textContent = '送審';
      }
    });

    dismissBtn.addEventListener('click', () => { block.remove(); });
  }

  // ── Results panel ─────────────────────────────────────────────────────────

  function appendResultCard(result, sql, dbName) {
    if (!resultsPanel || !resultsList) return 'da-result-0';
    resultsPanel.style.display = '';

    const cardId = 'da-result-' + Date.now();
    const card = document.createElement('div');
    card.className = 'da-result-card';
    card.id = cardId;

    const rowCount = (result.rows || []).length;
    const header = document.createElement('div');
    header.className = 'da-result-card-header';
    header.innerHTML = `
      <span class="da-result-meta">${dbName ? `[${escHtml(dbName)}] ` : ''}${rowCount} 筆</span>
      <button class="btn btn-ghost btn-sm da-result-csv-btn" title="匯出 CSV">↓ CSV</button>`;
    card.appendChild(header);

    if (sql) {
      const sqlDiv = document.createElement('div');
      sqlDiv.className = 'da-result-sql';
      sqlDiv.textContent = sql;
      card.appendChild(sqlDiv);
    }

    const tableWrap = document.createElement('div');
    tableWrap.className = 'da-result-table-wrap';
    tableWrap.innerHTML = renderResultTable(result);
    card.appendChild(tableWrap);

    resultsList.appendChild(card);

    header.querySelector('.da-result-csv-btn').addEventListener('click', () => downloadCsv(result, dbName));
    setTimeout(() => card.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 50);
    return cardId;
  }

  function downloadCsv(result, dbName) {
    const cols = result.columns || [];
    const rows = result.rows || [];
    const escape = s => `"${String(s == null ? '' : s).replace(/"/g, '""')}"`;
    const lines = [cols.map(escape).join(',')];
    rows.forEach(row => lines.push(row.map(escape).join(',')));
    const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = (dbName ? dbName + '_' : '') + 'result.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  if (resultsClearBtn) {
    resultsClearBtn.addEventListener('click', () => {
      if (resultsList) resultsList.innerHTML = '';
      if (resultsPanel) resultsPanel.style.display = 'none';
    });
  }

  // ── Design panel ──────────────────────────────────────────────────────────

  function openDesignPanel(ds) {
    if (!designPanel) return;
    const titleEl = document.getElementById('da-design-title');
    const replyEl = document.getElementById('da-design-reply');
    const tablesEl = document.getElementById('da-design-tables');
    const linkEl = document.getElementById('da-design-open-link');

    if (titleEl) titleEl.textContent = ds.title || '資料表設計';
    if (replyEl) replyEl.textContent = ds.reply || '';
    if (linkEl) {
      linkEl.href = ds.tables_ready
        ? `/sessions/${ds.id}/confirm`
        : `/sessions/${ds.id}/chat`;
    }
    if (tablesEl) {
      if (ds.tables_ready && ds.tables && ds.tables.length) {
        tablesEl.innerHTML = ds.tables.map(t => `
          <div class="da-design-table-card">
            <div class="da-design-table-name">${escHtml(t.table_name)}</div>
            ${t.description ? `<div class="da-design-table-desc">${escHtml(t.description)}</div>` : ''}
            <div class="da-design-table-cols">${(t.columns || []).slice(0, 6).map(c =>
              `<span class="da-design-col">${escHtml(c.name)}: ${escHtml(c.data_type)}</span>`
            ).join('')}${(t.columns || []).length > 6
              ? `<span class="da-design-col" style="color:var(--muted)">+${t.columns.length - 6} 欄</span>` : ''}</div>
          </div>`).join('');
      } else {
        tablesEl.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:8px 0;">正在整理需求中…</div>';
      }
    }

    designPanel.style.display = '';
    designPanel.setAttribute('aria-hidden', 'false');
  }

  const designCloseBtn = document.getElementById('da-design-close');
  if (designCloseBtn) {
    designCloseBtn.addEventListener('click', () => {
      if (designPanel) {
        designPanel.style.display = 'none';
        designPanel.setAttribute('aria-hidden', 'true');
      }
    });
  }

  // ── Pending change requests panel ────────────────────────────────────────

  const ADMIN_TOKEN_KEY = 'sqlagent_admin_token';
  const STATUS_LABELS = { pending: '待審', approved: '已核准', rejected: '已駁回', executed: '已執行', failed: '失敗' };

  if (adminTokenInput) {
    adminTokenInput.value = sessionStorage.getItem(ADMIN_TOKEN_KEY) || '';
    adminTokenInput.addEventListener('change', () => {
      sessionStorage.setItem(ADMIN_TOKEN_KEY, adminTokenInput.value);
    });
  }

  function renderPendingList(items) {
    if (!pendingListEl) return;
    if (!Array.isArray(items) || !items.length) {
      pendingListEl.innerHTML = '<div class="workbench-sidebar-loading">目前沒有待審的變更請求</div>';
      return;
    }
    pendingListEl.innerHTML = items.map(r => `
      <div class="da-pending-item" data-id="${escHtml(r.id)}">
        <div class="da-pending-item-meta">
          <span>${escHtml(r.db_name || '')}</span>
          <span class="da-pending-status da-pending-status-${escHtml(r.status)}">${escHtml(STATUS_LABELS[r.status] || r.status)}</span>
        </div>
        <pre class="da-ddl-pre">${escHtml(r.ddl)}</pre>
        ${r.reason ? `<div class="da-pending-reason">${escHtml(r.reason)}</div>` : ''}
        ${r.error ? `<div class="da-ddl-result err">✗ ${escHtml(r.error)}</div>` : ''}
        <div class="da-pending-actions">
          <button class="btn btn-primary btn-sm da-pending-approve">核准</button>
          <button class="btn btn-ghost btn-sm da-pending-reject">駁回</button>
        </div>
      </div>`).join('');

    pendingListEl.querySelectorAll('.da-pending-item').forEach(el => {
      const id = el.dataset.id;
      const approveBtn = el.querySelector('.da-pending-approve');
      const rejectBtn = el.querySelector('.da-pending-reject');
      if (approveBtn) approveBtn.addEventListener('click', () => decideChangeRequest(id, 'approve'));
      if (rejectBtn) rejectBtn.addEventListener('click', () => decideChangeRequest(id, 'reject'));
    });
  }

  function loadPendingRequests() {
    if (!pendingListEl) return;
    fetch('/api/change-requests?status=pending')
      .then(r => r.json())
      .then(renderPendingList)
      .catch(() => {});
  }

  async function decideChangeRequest(id, action) {
    const token = adminTokenInput ? adminTokenInput.value.trim() : '';
    try {
      const res = await fetch(`/api/change-requests/${id}/${action}`, {
        method: 'POST',
        headers: { 'X-Admin-Token': token },
      });
      const data = await res.json();
      if (!res.ok) {
        alert('操作失敗：' + (data.error || res.status));
        return;
      }
      loadPendingRequests();
      loadSchemaTree();
    } catch (e) {
      alert('連線失敗');
    }
  }

  if (pendingRefreshBtn) pendingRefreshBtn.addEventListener('click', loadPendingRequests);
  loadPendingRequests();

  // ── Check documentation completeness (quick button) ─────────────────────────

  if (checkDocsBtn) {
    checkDocsBtn.addEventListener('click', () => {
      inputEl.value = '請檢查目前資料庫的資料表與欄位說明是否完整，若有缺漏請草擬說明供我確認。';
      sendMessage();
    });
  }

  // ── Clear chat ────────────────────────────────────────────────────────────

  if (clearBtn) {
    clearBtn.addEventListener('click', async () => {
      if (!confirm('清除所有對話記錄？')) return;
      await fetch('/api/db-agent/chat', { method: 'DELETE' });
      messagesEl.innerHTML = '<div class="da-bubble da-bubble-ai">對話已清除。有什麼需要幫忙的嗎？</div>';
    });
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
    this.style.height = Math.min(this.scrollHeight, 140) + 'px';
  });
})();
