(function () {
  'use strict';

  const messagesEl = document.getElementById('da-messages');
  const inputEl = document.getElementById('da-input');
  const sendBtn = document.getElementById('da-send');
  const schemaTree = document.getElementById('da-schema-tree');
  const clearBtn = document.getElementById('da-clear-chat');
  const refreshBtn = document.getElementById('da-refresh-schema');
  const sidebarRefreshBtn = document.getElementById('da-sidebar-refresh');

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

  function renderResultTable(data) {
    if (!data || !data.columns) return '';
    const header = data.columns.map(c => `<th>${escHtml(c)}</th>`).join('');
    const body = (data.rows || []).map(row =>
      `<tr>${row.map(cell => `<td>${escHtml(cell == null ? '' : String(cell))}</td>`).join('')}</tr>`
    ).join('');
    const note = data.truncated ? `<div style="font-size:11px;color:var(--muted);padding:4px 0;">（結果已截斷）</div>` : '';
    return `${note}<div style="overflow-x:auto;"><table class="workbench-result-table"><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  // ── Schema tree ───────────────────────────────────────────────────────────

  function loadSchemaTree() {
    schemaTree.innerHTML = '<div class="workbench-sidebar-loading">載入中…</div>';
    fetch('/api/db-agent/schema-tree')
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          schemaTree.innerHTML = `<div class="workbench-sidebar-loading" style="color:var(--error);">${escHtml(data.error)}</div>`;
          return;
        }
        const tables = data.tables || [];
        if (!tables.length) {
          schemaTree.innerHTML = '<div class="workbench-sidebar-loading">（無資料表）</div>';
          return;
        }
        schemaTree.innerHTML = tables.map(t => `
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

        schemaTree.querySelectorAll('.wb-tree-table-name').forEach(el => {
          el.addEventListener('click', function () {
            const cols = this.nextElementSibling;
            const icon = this.querySelector('.wb-tree-icon');
            const open = cols.style.display !== 'none';
            cols.style.display = open ? 'none' : '';
            if (icon) icon.textContent = open ? '▶' : '▼';
          });
        });
      })
      .catch(() => {
        schemaTree.innerHTML = '<div class="workbench-sidebar-loading" style="color:var(--error);">載入失敗</div>';
      });
  }

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

    try {
      const res = await fetch('/api/db-agent/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      });
      const data = await res.json();
      removeById(thinkingId);

      if (!res.ok) {
        appendBubble('錯誤：' + (data.error || '請求失敗'), 'error');
        return;
      }

      // Plain reply bubble
      if (data.reply) appendBubble(data.reply, 'ai');

      // Query result
      if (data.query_result) {
        const wrapper = document.createElement('div');
        wrapper.className = 'da-bubble da-bubble-ai';
        if (data.query_sql) {
          const sqlPre = document.createElement('div');
          sqlPre.className = 'da-query-sql';
          sqlPre.textContent = data.query_sql;
          wrapper.appendChild(sqlPre);
        }
        const resultDiv = document.createElement('div');
        resultDiv.className = 'da-query-result';
        resultDiv.innerHTML = renderResultTable(data.query_result);
        wrapper.appendChild(resultDiv);
        messagesEl.appendChild(wrapper);
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }
      if (data.query_error) {
        appendBubble('查詢錯誤：' + data.query_error, 'error');
      }

      // DDL suggestion
      if (data.ddl_suggestion) {
        appendDdlBlock(data.ddl_suggestion);
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

  function appendDdlBlock(ddl) {
    const block = document.createElement('div');
    block.className = 'da-ddl-block';

    const label = document.createElement('div');
    label.className = 'da-ddl-label';
    label.textContent = 'AI 建議的 DDL（請確認後執行）：';
    block.appendChild(label);

    const pre = document.createElement('pre');
    pre.className = 'da-ddl-pre';
    pre.textContent = ddl;
    block.appendChild(pre);

    const actions = document.createElement('div');
    actions.className = 'da-ddl-actions';
    const runBtn = document.createElement('button');
    runBtn.className = 'btn btn-primary btn-sm';
    runBtn.textContent = '執行 DDL';
    const dismissBtn = document.createElement('button');
    dismissBtn.className = 'btn btn-ghost btn-sm';
    dismissBtn.textContent = '取消';
    const resultEl = document.createElement('div');
    resultEl.className = 'da-ddl-result';
    actions.appendChild(runBtn);
    actions.appendChild(dismissBtn);
    block.appendChild(actions);
    block.appendChild(resultEl);

    messagesEl.appendChild(block);
    messagesEl.scrollTop = messagesEl.scrollHeight;

    runBtn.addEventListener('click', async () => {
      runBtn.disabled = true;
      runBtn.textContent = '⟳ 執行中…';
      resultEl.textContent = '';
      resultEl.className = 'da-ddl-result';
      try {
        const res = await fetch('/api/db-agent/execute-ddl', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ddl }),
        });
        const data = await res.json();
        if (data.ok) {
          resultEl.textContent = `✓ 已執行 ${data.statements_run} 條語句`;
          resultEl.classList.add('ok');
          loadSchemaTree();
        } else {
          resultEl.textContent = '✗ ' + (data.error || '執行失敗');
          resultEl.classList.add('err');
        }
      } catch (e) {
        resultEl.textContent = '✗ 連線失敗';
        resultEl.classList.add('err');
      } finally {
        runBtn.disabled = false;
        runBtn.textContent = '執行 DDL';
      }
    });

    dismissBtn.addEventListener('click', () => { block.remove(); });
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
