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
