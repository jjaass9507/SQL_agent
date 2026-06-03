(function () {
  'use strict';

  // Only activate when a live DB is connected
  if (typeof HAS_DB === 'undefined' || !HAS_DB) return;

  const panel = document.getElementById('schema-chat-panel');
  const toggleBtn = document.getElementById('schema-chat-toggle');
  const closeBtn = document.getElementById('schema-chat-close');
  const messagesEl = document.getElementById('schema-chat-messages');
  const inputEl = document.getElementById('schema-chat-input');
  const sendBtn = document.getElementById('schema-chat-send');
  const ddlConfirm = document.getElementById('schema-chat-ddl-confirm');
  const ddlCode = document.getElementById('schema-chat-ddl-code');
  const ddlRunBtn = document.getElementById('schema-chat-ddl-run');
  const ddlDismissBtn = document.getElementById('schema-chat-ddl-dismiss');
  const ddlResult = document.getElementById('schema-chat-ddl-result');

  // Show toggle button
  toggleBtn.style.display = '';

  toggleBtn.addEventListener('click', openPanel);
  closeBtn.addEventListener('click', closePanel);

  function openPanel() {
    panel.classList.add('open');
    panel.setAttribute('aria-hidden', 'false');
    inputEl.focus();
  }

  function closePanel() {
    panel.classList.remove('open');
    panel.setAttribute('aria-hidden', 'true');
  }

  // ── Message helpers ──────────────────────────────────

  function escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
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

  // ── Send message ─────────────────────────────────────

  async function sendMessage() {
    const text = (inputEl.value || '').trim();
    if (!text) return;
    inputEl.value = '';
    inputEl.style.height = '';

    appendBubble(text, 'user');
    hideDdlConfirm();

    sendBtn.disabled = true;
    const thinkingId = 'sc-thinking-' + Date.now();
    appendBubble('⟳ 思考中…', 'thinking', thinkingId);

    try {
      const res = await fetch(`/api/sessions/${SESSION_ID}/schema-chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      });
      const data = await res.json();
      removeById(thinkingId);

      if (!res.ok) {
        appendBubble('錯誤：' + (data.error || '請求失敗'), 'error');
      } else {
        appendBubble(data.reply, 'ai');
        if (data.ddl_suggestion) {
          showDdlConfirm(data.ddl_suggestion);
        }
      }
    } catch (e) {
      removeById(thinkingId);
      appendBubble('連線失敗，請重新整理頁面後再試', 'error');
    } finally {
      sendBtn.disabled = false;
      inputEl.focus();
    }
  }

  // ── DDL confirm block ─────────────────────────────────

  function showDdlConfirm(ddl) {
    ddlCode.textContent = ddl;
    ddlResult.textContent = '';
    ddlResult.className = 'schema-chat-ddl-result';
    ddlRunBtn.disabled = false;
    ddlRunBtn.textContent = '執行 DDL';
    ddlConfirm.style.display = '';
    ddlConfirm.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

    ddlRunBtn.onclick = () => executeDdl(ddl);
  }

  function hideDdlConfirm() {
    ddlConfirm.style.display = 'none';
  }

  ddlDismissBtn.addEventListener('click', hideDdlConfirm);

  async function executeDdl(ddl) {
    ddlRunBtn.disabled = true;
    ddlRunBtn.textContent = '⟳ 執行中…';
    ddlResult.textContent = '';
    ddlResult.className = 'schema-chat-ddl-result';

    try {
      const res = await fetch(`/api/sessions/${SESSION_ID}/execute-schema-ddl`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ddl }),
      });
      const data = await res.json();

      if (data.ok) {
        ddlResult.textContent = `✓ 已執行 ${data.statements_run} 條語句`;
        ddlResult.classList.add('ok');
        appendBubble(`✓ DDL 執行成功（${data.statements_run} 條語句）`, 'ai');
        // Refresh workbench schema tree if it is loaded
        if (typeof loadSchemaTree === 'function') loadSchemaTree();
      } else {
        ddlResult.textContent = '✗ ' + (data.error || '執行失敗');
        ddlResult.classList.add('err');
      }
    } catch (e) {
      ddlResult.textContent = '✗ 連線失敗';
      ddlResult.classList.add('err');
    } finally {
      ddlRunBtn.disabled = false;
      ddlRunBtn.textContent = '執行 DDL';
    }
  }

  // ── Input events ─────────────────────────────────────

  sendBtn.addEventListener('click', sendMessage);

  inputEl.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  // Auto-resize textarea
  inputEl.addEventListener('input', function () {
    this.style.height = '';
    this.style.height = Math.min(this.scrollHeight, 120) + 'px';
  });
})();
