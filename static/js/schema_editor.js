// Inline schema editor for the confirm page.
// Builds an editable form from INITIAL_TABLES, saves via PUT /api/sessions/<id>/tables.

const COMMON_TYPES = ['uuid', 'serial', 'bigserial', 'integer', 'bigint', 'smallint',
  'numeric', 'decimal', 'real', 'double precision', 'varchar', 'text', 'char',
  'boolean', 'date', 'time', 'timestamp', 'timestamptz', 'json', 'jsonb', 'bytea'];

let editTables = null;  // working copy while editing

function escAttr(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/"/g, '&quot;')
    .replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function startEdit() {
  editTables = JSON.parse(JSON.stringify(INITIAL_TABLES || []));
  document.getElementById('schema-readonly').style.display = 'none';
  const advisor = document.getElementById('advisor-section');
  if (advisor) advisor.style.display = 'none';
  const editorEl = document.getElementById('schema-editor');
  editorEl.style.display = 'block';
  renderEditor();
  const btn = document.getElementById('edit-schema-btn');
  if (btn) btn.style.display = 'none';
}

function cancelEdit() {
  editTables = null;
  document.getElementById('schema-editor').style.display = 'none';
  document.getElementById('schema-readonly').style.display = '';
  const advisor = document.getElementById('advisor-section');
  if (advisor) advisor.style.display = '';
  const btn = document.getElementById('edit-schema-btn');
  if (btn) btn.style.display = '';
}

function renderEditor() {
  const el = document.getElementById('schema-editor');
  const dl = `<datalist id="type-options">${COMMON_TYPES.map(t => `<option value="${t}">`).join('')}</datalist>`;
  const tablesHtml = editTables.map((t, ti) => `
    <div class="edit-table">
      <div class="edit-table-head">
        <input class="edit-input edit-tname" value="${escAttr(t.table_name)}"
               placeholder="資料表名稱" oninput="setTableField(${ti},'table_name',this.value)">
        <input class="edit-input edit-tdesc" value="${escAttr(t.description)}"
               placeholder="用途說明" oninput="setTableField(${ti},'description',this.value)">
        <button class="btn btn-danger btn-sm" onclick="removeTable(${ti})" title="刪除此表">🗑</button>
      </div>
      <div class="edit-col-grid edit-col-grid-head">
        <span>欄位名</span><span>型態</span><span>長度</span>
        <span title="允許 NULL">NULL</span><span title="主鍵">PK</span><span title="外鍵">FK</span>
        <span title="參照">參照</span><span title="唯一">UQ</span><span title="索引">IDX</span>
        <span>說明</span><span></span>
      </div>
      ${t.columns.map((c, ci) => colRow(ti, ci, c)).join('')}
      <button class="btn btn-ghost btn-sm edit-add-col" onclick="addColumn(${ti})">＋ 新增欄位</button>
    </div>
  `).join('');
  el.innerHTML = dl + tablesHtml + `
    <div class="edit-actions-row">
      <button class="btn btn-ghost btn-sm" onclick="addTable()">＋ 新增資料表</button>
      <div style="flex:1;"></div>
      <button class="btn btn-ghost" onclick="cancelEdit()">取消</button>
      <button class="btn btn-success" id="save-schema-btn" onclick="saveSchema()">💾 儲存修改</button>
    </div>
    <div id="edit-error" style="color:var(--error);font-size:13px;margin-top:6px;"></div>`;
}

function colRow(ti, ci, c) {
  const chk = (field) => `<input type="checkbox" ${c[field] ? 'checked' : ''}
    onchange="setColField(${ti},${ci},'${field}',this.checked)">`;
  return `
    <div class="edit-col-grid">
      <input class="edit-input" value="${escAttr(c.name)}" placeholder="name"
             oninput="setColField(${ti},${ci},'name',this.value)">
      <input class="edit-input" list="type-options" value="${escAttr(c.data_type)}" placeholder="type"
             oninput="setColField(${ti},${ci},'data_type',this.value)">
      <input class="edit-input edit-len" type="number" value="${c.length == null ? '' : c.length}"
             oninput="setColField(${ti},${ci},'length',this.value)">
      <span class="edit-chk">${chk('nullable')}</span>
      <span class="edit-chk">${chk('is_primary_key')}</span>
      <span class="edit-chk">${chk('is_foreign_key')}</span>
      <input class="edit-input edit-ref" value="${escAttr(c.references)}" placeholder="table.col"
             oninput="setColField(${ti},${ci},'references',this.value)">
      <span class="edit-chk">${chk('is_unique')}</span>
      <span class="edit-chk">${chk('is_indexed')}</span>
      <input class="edit-input" value="${escAttr(c.description)}" placeholder="說明"
             oninput="setColField(${ti},${ci},'description',this.value)">
      <button class="edit-col-remove" onclick="removeColumn(${ti},${ci})" title="刪除欄位">✕</button>
    </div>`;
}

function setTableField(ti, field, val) { editTables[ti][field] = val; }

function setColField(ti, ci, field, val) {
  if (field === 'length') {
    editTables[ti].columns[ci].length = val === '' ? null : parseInt(val, 10);
  } else {
    editTables[ti].columns[ci][field] = val;
  }
}

function addColumn(ti) {
  editTables[ti].columns.push({
    name: '', data_type: 'text', length: null, nullable: true, default: null,
    description: '', is_primary_key: false, is_foreign_key: false,
    references: null, is_unique: false, is_indexed: false,
  });
  renderEditor();
}

function removeColumn(ti, ci) {
  editTables[ti].columns.splice(ci, 1);
  renderEditor();
}

function addTable() {
  editTables.push({
    table_name: '', description: '', constraints: [], related_tables: [],
    columns: [{
      name: 'id', data_type: 'uuid', length: null, nullable: false, default: null,
      description: '主鍵', is_primary_key: true, is_foreign_key: false,
      references: null, is_unique: false, is_indexed: false,
    }],
  });
  renderEditor();
}

function removeTable(ti) {
  if (editTables.length <= 1) {
    document.getElementById('edit-error').textContent = '⚠ 至少需保留一張資料表';
    return;
  }
  editTables.splice(ti, 1);
  renderEditor();
}

async function saveSchema() {
  const errEl = document.getElementById('edit-error');
  errEl.textContent = '';
  // Client-side validation: names present
  for (const t of editTables) {
    if (!t.table_name.trim()) { errEl.textContent = '⚠ 每張資料表都需要名稱'; return; }
    if (!t.columns.length) { errEl.textContent = `⚠ 資料表「${t.table_name}」至少需要一個欄位`; return; }
    for (const c of t.columns) {
      if (!c.name.trim()) { errEl.textContent = `⚠ 資料表「${t.table_name}」有欄位未命名`; return; }
    }
  }
  const btn = document.getElementById('save-schema-btn');
  btn.disabled = true;
  btn.textContent = '儲存中...';
  try {
    const res = await fetch(`/api/sessions/${SESSION_ID}/tables`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tables: editTables }),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.error || '儲存失敗');
    }
    window.location.reload();  // refresh diff, warnings, version history
  } catch (e) {
    btn.disabled = false;
    btn.textContent = '💾 儲存修改';
    errEl.textContent = '⚠ ' + e.message;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('edit-schema-btn');
  if (btn) btn.addEventListener('click', startEdit);
});
