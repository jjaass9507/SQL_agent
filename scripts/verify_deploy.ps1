# 在正式機執行，確認「執行中的到底是哪一份程式」並更新到最新。
# 回答那個反覆出現的問題：「這 bug 明明修好了，怎麼又出現？」——幾乎都是
# checkout 沒更新（停在舊 branch/commit），或 venv 的 site-packages 裡有殘留舊副本。
$ErrorActionPreference = "Stop"

# 找專案根目錄（含 pyproject.toml 的資料夾）
$Root = $PSScriptRoot
while ($Root -and -not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    $Root = Split-Path $Root -Parent
}
if (-not $Root) { Write-Error "找不到專案根目錄（缺 pyproject.toml）"; exit 1 }
Set-Location $Root

Write-Host "=== 1. 目前 checkout ==="
git branch --show-current
git log --oneline -1

Write-Host "`n=== 2. 更新到目前分支最新 ==="
git pull
git log --oneline -1

Write-Host "`n=== 3. 確認 venv import 的是「這棵原始碼樹」（非 site-packages 殘留）==="
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$importPath = & $py -c "import app; print(app.__file__)"
Write-Host "app import 自：$importPath"
$expected = Join-Path $Root "app"
if ($importPath -like "$expected*") {
    Write-Host "OK：venv 指向 live 原始碼樹——git pull 的更新會生效。" -ForegroundColor Green
    Write-Host "`n完成。若本次 pull 含 schema 變動，先跑：" -ForegroundColor Yellow
    Write-Host "  .\.venv\Scripts\python.exe -m alembic upgrade head" -ForegroundColor Yellow
    Write-Host "接著回收 IIS 應用程式集區（或 iisreset）讓新程式碼載入。"
} else {
    Write-Host "問題：app 是從本 repo 以外載入（多半是 site-packages 殘留舊副本）。" -ForegroundColor Red
    Write-Host "git pull 不會更新那份副本。" -ForegroundColor Red
    Write-Host "修法：重跑 scripts\install_offline.ps1 重建 venv，再回收 IIS 應用程式集區。" -ForegroundColor Red
    exit 1
}
