const messagesEl = document.getElementById('chat-messages');
const inputEl = document.getElementById('chat-input');
const sendBtn = document.getElementById('send-btn');
const inputRow = document.getElementById('input-row');
const progressList = document.getElementById('progress-list');
const welcomeMsg = document.getElementById('welcome-msg');

let isLoading = false;

// Render existing messages on load
if (EXISTING_MESSAGES && EXISTING_MESSAGES.length > 0) {
  welcomeMsg.remove();
  EXISTING_MESSAGES.forEach(m => appendBubble(m.role, m.content));
}

function appendBubble(role, content) {
  const row = document.createElement('div');
  row.className = `msg-row${role === 'user' ? ' user' : ''}`;
  row.innerHTML = `
    <div class="msg-avatar">${role === 'ai' ? 'AI' : 'PM'}</div>
    <div class="msg-bubble">${escHtml(content).replace(/\n/g, '<br>')}</div>`;
  messagesEl.appendChild(row);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return row;
}

function appendTyping() {
  const row = document.createElement('div');
  row.className = 'msg-row';
  row.id = 'typing-indicator';
  row.innerHTML = `<div class="msg-avatar">AI</div><div class="msg-bubble"><span class="typing-dots">●●●</span></div>`;
  messagesEl.appendChild(row);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return row;
}

function setLoading(val) {
  isLoading = val;
  inputEl.disabled = val;
  sendBtn.disabled = val;
  inputRow.classList.toggle('disabled', val);
}

async function sendMessage() {
  const content = inputEl.value.trim();
  if (!content || isLoading) return;

  if (welcomeMsg.parentNode) welcomeMsg.remove();
  inputEl.value = '';
  appendBubble('user', content);
  const typingRow = appendTyping();
  setLoading(true);

  try {
    const res = await fetch(`/api/sessions/${SESSION_ID}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    const data = await res.json();
    typingRow.remove();

    if (data.reply) appendBubble('ai', data.reply);

    if (data.tables_ready) {
      setTimeout(() => {
        window.location.href = `/sessions/${SESSION_ID}/confirm`;
      }, 1200);
    } else if (data.tables) {
      updateProgressPanel(data.tables);
    }
  } catch (e) {
    typingRow.remove();
    appendBubble('ai', '抱歉，發生錯誤，請稍後再試。');
  } finally {
    setLoading(false);
    inputEl.focus();
  }
}

function updateProgressPanel(tables) {
  if (!tables || tables.length === 0) return;
  progressList.innerHTML = tables.map(t => {
    const fields = t.columns.slice(0, 4).map(c => `${c.name}${c.is_primary_key ? ' (PK)' : c.is_foreign_key ? ' (FK)' : ''}`);
    return `
      <div class="progress-item">
        <div class="progress-item-header">
          <span class="progress-status done">✓</span>
          <span class="progress-name">${escHtml(t.table_name)}</span>
        </div>
        ${fields.map(f => `<div class="progress-field">· ${escHtml(f)}</div>`).join('')}
        ${t.columns.length > 4 ? `<div class="progress-field" style="color:var(--muted)">... 共 ${t.columns.length} 個欄位</div>` : ''}
      </div>`;
  }).join('');
}

// Send on button click
sendBtn.addEventListener('click', sendMessage);

// Send on Enter, Shift+Enter for newline
inputEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// Auto-resize textarea
inputEl.addEventListener('input', () => {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
});

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

inputEl.focus();
