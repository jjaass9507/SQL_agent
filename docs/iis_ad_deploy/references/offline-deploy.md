# 離線套件部署參考

部署機無法連網時，在開發機打包 wheel 套件後用 USB 傳入。

---

## 黃金法則：venv 不可搬移

venv 在建立時寫死了 Python 的絕對路徑。複製或搬移 venv 到不同路徑或機器，
會出現：`Fatal error in launcher: Unable to create process using '舊路徑\python.exe'`

**正確做法**：
- 搬移的是：**source code + requirements.txt + wheels/**
- 不搬移：`venv/`、`.env`
- 在目標機器的最終路徑重建 venv

---

## 開發機：打包 wheel

先確認部署機的 Python 版本，再打包相應版本的 wheel：

```powershell
# 確認部署機版本（在部署機執行）
python --version   # e.g. Python 3.11.x

# 回到開發機，用相同版本打包
cd <你的專案目錄>

pip download `
  -r requirements.txt `
  -d wheels `
  --platform win_amd64 `
  --python-version 3.11 `
  --only-binary=:all:
```

**`--only-binary=:all:` 的作用**：只下載預編譯的 `.whl` 檔，
不下載需要在目標機器編譯的 sdist（`.tar.gz`）。
若某個套件沒有 Windows 的預編譯版本，會報錯，需要另行處理。

完成後 `wheels/` 內有所有依賴的 `.whl` 檔（含間接依賴）。

---

## 傳輸清單

```
你的專案/
├── app.py, wsgi.py, ...    ← source code
├── requirements.txt
├── .env.example             ← 範本（不含密碼）
├── wheels/                  ← pip download 的結果
└── web.config               ← 新增的 IIS 設定
```

**不傳**：
- `venv/`（在部署機重建）
- `.env`（在部署機手動填入，含機密）
- `.git/`

---

## 部署機：離線安裝

```powershell
cd D:\WebServices\my-app

# 建立全新 venv
python -m venv venv

# 從本地 wheels/ 安裝，完全不需要連網
.\venv\Scripts\pip install `
  --no-index `
  --find-links=wheels `
  -r requirements.txt

# 驗證安裝完整
.\venv\Scripts\pip list

# 冒煙測試（手動確認能啟動）
.\venv\Scripts\python.exe -m waitress --port=9099 wsgi:application
# 看到 "Serving on http://0.0.0.0:9099" = 成功，Ctrl+C 停止
```

---

## 後續更新套件

```powershell
# 開發機：修改 requirements.txt 後重新打包
pip download -r requirements.txt -d wheels --platform win_amd64 --python-version 3.11 --only-binary=:all:

# 傳輸新的 wheels/ 到部署機
# 部署機：重新安裝（venv 不需要重建）
.\venv\Scripts\pip install --no-index --find-links=wheels -r requirements.txt
```

---

## 常見問題

### 某個套件找不到 wheel

```
ERROR: Could not find a version that satisfies the requirement <pkg>
```

可能原因：
1. 該套件沒有 Windows 預編譯版本 → 嘗試移除 `--only-binary=:all:`，讓 pip 下載 sdist
2. Python 版本不符 → 確認 `--python-version` 與部署機一致
3. wheels/ 沒有該套件的間接依賴 → 用 `pip download` 重新打包整份

### 已安裝舊版本，pip 不更新

```powershell
# 強制重新安裝
.\venv\Scripts\pip install --no-index --find-links=wheels --force-reinstall -r requirements.txt
```

### 部署機 Python 版本比開發機新

wheel 向前相容有限，建議開發機和部署機用相同 minor version（例如都用 3.11.x）。
若版本差異是 minor（3.11 vs 3.12），大多套件的 pure Python wheel（`py3-none-any.whl`）仍可用，
但 C extension wheel 需要版本匹配。
