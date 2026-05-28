const loadingView = document.getElementById('loading-view');
const doneView = document.getElementById('done-view');
const topbarBadge = document.getElementById('topbar-badge');
const downloadBtn = document.getElementById('download-btn');
const reviewContent = document.getElementById('review-content');

let pollTimer = null;
let pollFailCount = 0;

function showReport(reportText) {
  topbarBadge.textContent = '✓ 審查完成';
  topbarBadge.className = 'badge badge-done';
  downloadBtn.style.display = '';
  reviewContent.innerHTML = renderMarkdown(reportText);
  loadingView.classList.add('hidden');
  doneView.classList.remove('hidden');
}

async function poll() {
  try {
    const res = await fetch(`/api/sessions/${SESSION_ID}`);
    const session = await res.json();
    pollFailCount = 0;

    if (session.phase === 'review_done') {
      clearInterval(pollTimer);
      const report = (session.outputs || {})['05_review_report.md'] || '（無報告內容）';
      showReport(report);
    }
  } catch (e) {
    console.error('poll error', e);
    pollFailCount++;
    if (pollFailCount >= 3) {
      clearInterval(pollTimer);
      loadingView.innerHTML = '<div style="text-align:center;padding:40px;color:var(--error);">⚠ 連線中斷，請重新整理頁面</div>';
    }
  }
}

// Download report as .md
downloadBtn.addEventListener('click', async () => {
  const res = await fetch(`/api/sessions/${SESSION_ID}`);
  const session = await res.json();
  const report = (session.outputs || {})['05_review_report.md'] || '';
  if (!report) return;
  const blob = new Blob([report], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = '審查報告.md';
  a.click();
  URL.revokeObjectURL(a.href);
});

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function renderMarkdown(md) {
  return escHtml(md)
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>\n?)+/g, m => `<ul>${m}</ul>`)
    .replace(/\n/g, '<br>');
}

if (INITIAL_PHASE === 'review_done') {
  poll(); // single fetch to get report immediately
} else {
  pollTimer = setInterval(poll, 2000);
  poll();
}
