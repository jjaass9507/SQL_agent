// "Related to existing DB" panel — related tables / FK suggestions / duplicate
// risks from web/table_relation.py. Stays empty (no visible box) when there's
// nothing to show.
function escHtml(s) {
  return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

(function renderRelationReport() {
  const container = document.getElementById('relation-section');
  if (!container || typeof RELATION_REPORT === 'undefined' || !RELATION_REPORT) return;

  const related = RELATION_REPORT.related || [];
  const fkSuggestions = RELATION_REPORT.fk_suggestions || [];
  const duplicateRisks = RELATION_REPORT.duplicate_risks || [];
  if (!related.length && !fkSuggestions.length && !duplicateRisks.length) return;

  let html = '<div class="diff-section-title">🔗 與現有資料庫的關聯</div>';

  if (related.length) {
    html += `<div class="diff-group"><div class="diff-group-label">相關資料表（${related.length}）</div>` +
      related.map(r => `<div class="diff-item">
        <span class="diff-tag diff-tag-same">相關</span>
        <span class="diff-item-name">${escHtml(r.table)}</span>
        <span>${escHtml(r.reason || '')}</span>
      </div>`).join('') + '</div>';
  }
  if (fkSuggestions.length) {
    html += `<div class="diff-group"><div class="diff-group-label">建議外鍵（${fkSuggestions.length}）</div>` +
      fkSuggestions.map(f => `<div class="diff-item">
        <span class="diff-tag diff-tag-new">FK</span>
        <span class="diff-item-name">${escHtml(f.from_table)}.${escHtml(f.column)}</span>
        <span>→ ${escHtml(f.to_table)}</span>
      </div>`).join('') + '</div>';
  }
  if (duplicateRisks.length) {
    html += `<div class="diff-group"><div class="diff-group-label">重複建表風險（${duplicateRisks.length}）</div>` +
      duplicateRisks.map(d => `<div class="diff-item">
        <span class="diff-tag diff-tag-dropped">重複</span>
        <span class="diff-item-name">${escHtml(d.design_table)}</span>
        <span>與現有 ${escHtml(d.existing_table)} 欄位重疊 ${Math.round((d.overlap || 0) * 100)}%</span>
      </div>`).join('') + '</div>';
  }

  container.classList.add('diff-section');
  container.innerHTML = html;
})();

// Version restore
document.querySelectorAll('.version-chip').forEach(chip => {
  chip.addEventListener('click', async () => {
    const version = chip.dataset.version;
    if (chip.classList.contains('current') || chip.classList.contains('restoring')) return;
    chip.classList.add('restoring');
    chip.textContent = '還原中...';
    try {
      const res = await fetch(`/api/sessions/${SESSION_ID}/versions/${version}/restore`, { method: 'POST' });
      if (!res.ok) throw new Error('restore failed');
      window.location.reload();
    } catch (e) {
      chip.classList.remove('restoring');
      chip.textContent = chip.dataset.label || `版本 ${version}`;
      showConfirmError('版本還原失敗，請稍後再試');
    }
  });
});

function showConfirmError(msg) {
  let el = document.getElementById('confirm-error-msg');
  if (!el) {
    el = document.createElement('div');
    el.id = 'confirm-error-msg';
    el.style.cssText = 'text-align:center;color:var(--error);font-size:13px;padding:6px 0;';
    document.querySelector('.confirm-footer').insertAdjacentElement('afterend', el);
  }
  el.textContent = '⚠ ' + msg;
  setTimeout(() => { if (el) el.textContent = ''; }, 4000);
}

document.getElementById('confirm-btn').addEventListener('click', async () => {
  const btn = document.getElementById('confirm-btn');
  btn.disabled = true;
  btn.textContent = '⟳ 啟動產出中...';

  try {
    const res = await fetch(`/api/sessions/${SESSION_ID}/confirm`, { method: 'POST' });
    if (res.status === 404) {
      window.location.href = '/';
      return;
    }
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || '確認失敗');
    }
    window.location.href = `/sessions/${SESSION_ID}/docs`;
  } catch (e) {
    btn.disabled = false;
    btn.textContent = '✓ 確認，開始產出文件';
    showConfirmError(e.message || '請稍後再試');
  }
});
