# 在「離線的正式內網 Windows 機器」上執行，從 offline_packages\ 離線安裝。
# 先把 pack_offline.ps1 產生的 sql-agent-offline.zip 解壓到最終部署路徑
# （例如 C:\inetpub\sql_agent），再在該路徑內執行本腳本。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\install_offline.ps1
$ErrorActionPreference = "Stop"

# 找專案根目錄（含 pyproject.toml 的資料夾）
$Root = $PSScriptRoot
while ($Root -and -not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    $Root = Split-Path $Root -Parent
}
if (-not $Root) { Write-Error "找不到專案根目錄（缺 pyproject.toml）"; exit 1 }
Set-Location $Root

# 確認 Python 3.11+
$ver = python --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "找不到 Python。請先安裝 Python 3.11+（https://www.python.org/downloads/）"
    exit 1
}
Write-Host "使用 $ver"

$PkgDir = Join-Path $Root "offline_packages"
if (-not (Test-Path $PkgDir)) {
    Write-Error "找不到 offline_packages。請先在連外機器跑 pack_offline.ps1。"
    exit 1
}

# 從別處複製來的 venv 一定壞掉（pip.exe 內嵌了原始 python.exe 絕對路徑），
# 一律在最終路徑就地重建。
$VenvDir = Join-Path $Root ".venv"
if (Test-Path $VenvDir) {
    Write-Host "移除既有 .venv（不能跨機器/路徑重用）..."
    Remove-Item $VenvDir -Recurse -Force
}
Write-Host "建立虛擬環境..."
python -m venv $VenvDir

# 用 'python -m pip'（不要用 pip.exe），全新 venv 也能運作。
$VenvPy = Join-Path $VenvDir "Scripts\python.exe"

# venv 的 Python 版本必須與「打包機」相同——offline_packages 內的 psycopg2 / asyncpg
# 是編譯 wheel（cp310 / cp311...），版本不符會裝不上。印出來供比對。
Write-Host "venv Python：$(& $VenvPy --version)（須與打包機相同，否則編譯 wheel 裝不上）"

# 1. 從離線 wheel 安裝本專案 + 全部依賴（含 postgres extra 的 psycopg2/asyncpg）。
#    以「名稱[extra]」安裝而非直接指定 wheel 檔，pip 才會套用 [postgres] extra。
& $VenvPy -m pip install --no-index --find-links="$PkgDir" "sql-agent[postgres]"
if ($LASTEXITCODE -ne 0) {
    Write-Error "離線安裝失敗。最常見原因：venv 的 Python 版本與打包機不符（上方版本），導致 psycopg2/asyncpg 找不到相容 wheel。請用相同版本的 Python 重建 venv。"
    exit 1
}

# 2. 讓「原始碼樹」成為權威來源：往後只要 git pull（或覆蓋解壓新包）即更新執行中的
#    程式，不必重裝——這是「我改了程式卻沒生效」的頭號原因。
#    作法：卸載剛裝的套件（保留其所有依賴），改寫一個 .pth 把 repo 根放進 import 路徑，
#    之後 'import app' 直接解析到原始碼樹的 app\。此法不需離線端具備 build 後端。
Write-Host ""
Write-Host "指向原始碼樹（往後 git pull 即可更新）..."
& $VenvPy -m pip uninstall -y sql-agent
$SitePackages = & $VenvPy -c "import sysconfig; print(sysconfig.get_path('purelib'))"
Set-Content -Path (Join-Path $SitePackages "app.pth") -Value $Root -Encoding ASCII
Write-Host "已寫入 $SitePackages\app.pth -> $Root"

# 驗證：載入 app.config（不是空的 app/__init__.py！）以實際觸發依賴 import，
# 並顯式檢查關鍵依賴——這樣依賴缺漏會「當場」爆，而非拖到 alembic 才發現。
& $VenvPy -c "import app.config, pydantic_settings, alembic, psycopg2, asyncpg, pathlib; print('OK：app.config 載入自', pathlib.Path(app.config.__file__).parent)"
if ($LASTEXITCODE -ne 0) {
    Write-Error "venv 無法載入 app.config 或缺少依賴。多半是 .pth 沒指到原始碼樹、或依賴沒裝進 venv。請檢查上方錯誤後重跑。"
    exit 1
}

Write-Host ""
Write-Host "安裝完成。後續步驟："
Write-Host "  1. copy .env.example .env"
Write-Host "  2. 編輯 .env：SECRET_KEY / DB_ENCRYPTION_KEY / DATABASE_URL / LLM_* /"
Write-Host "     AUTH_ENABLED=true 及 AD_* （AD SSO 見 docs\deployment.md 第 4 節）"
Write-Host "  3. 建立資料表：.\.venv\Scripts\python.exe -m alembic upgrade head"
Write-Host "  4. 由 IIS 經 web.config 拉起 uvicorn（見 docs\deployment.md 4-2）；"
Write-Host "     web.config 的 processPath 指向本 .venv\Scripts\python.exe"
Write-Host ""
Write-Host "往後更新：git pull（或覆蓋解壓新包）即可。"
Write-Host "只有 pyproject.toml 依賴變動時才需重跑本腳本；schema 變動時記得再 alembic upgrade head。"
