document.getElementById('confirm-btn').addEventListener('click', async () => {
  const btn = document.getElementById('confirm-btn');
  btn.disabled = true;
  btn.textContent = '⟳ 啟動產出中...';

  try {
    const res = await fetch(`/api/sessions/${SESSION_ID}/confirm`, { method: 'POST' });
    if (!res.ok) throw new Error('confirm failed');
    window.location.href = `/sessions/${SESSION_ID}/docs`;
  } catch (e) {
    btn.disabled = false;
    btn.textContent = '✓ 確認，開始產出文件';
    alert('確認失敗，請稍後再試');
  }
});
