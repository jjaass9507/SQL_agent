# 在一台「能連外、且 Python 版本與正式機相同」的 Windows 機器上執行。
# 把本專案所有依賴（含 postgres extra）下載成 wheel、連同原始碼打包成一個 zip，
# 供無法連外的正式內網機器離線安裝（見 scripts\install_offline.ps1）。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\pack_offline.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\pack_offline.ps1 -Proxy http://proxy:8080
param([string]$Proxy = "")
$ErrorActionPreference = "Stop"

# 找專案根目錄（含 pyproject.toml 的資料夾）
$Root = $PSScriptRoot
while ($Root -and -not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    $Root = Split-Path $Root -Parent
}
if (-not $Root) { Write-Error "找不到專案根目錄（缺 pyproject.toml）"; exit 1 }
Set-Location $Root

# 解析 proxy
if (-not $Proxy) { $Proxy = $env:HTTPS_PROXY }
if (-not $Proxy) { $Proxy = $env:HTTP_PROXY }
$ProxyArg = if ($Proxy) { @("--proxy", $Proxy) } else { @() }

# 打包用的 build 後端（本專案用 setuptools；建 wheel 時需要）
pip install @ProxyArg --upgrade pip setuptools wheel

$PkgDir = Join-Path $Root "offline_packages"
New-Item -ItemType Directory -Force -Path $PkgDir | Out-Null

# 下載所有執行期依賴為 wheel。務必帶 [postgres] extra，否則正式機少了
# psycopg2-binary / asyncpg，新增業務 DB 會報「No module named 'psycopg2'」，
# 若記憶後端用 PostgreSQL 也會連不上。直接讀 pyproject.toml（pip download），
# 之後新增依賴會自動被打包，不必手改清單。
pip download @ProxyArg ".[postgres]" -d $PkgDir

# 把本專案自身也 build 成 wheel（離線端據此安裝，再切換成 live source，見 install 腳本）
pip wheel . --no-deps --no-build-isolation -w $PkgDir

# 打包原始碼 zip，排除 .venv / .git / 快取 / 本機資料與密鑰。
# 注意：.venv 不可打包（venv 內的 .exe 內嵌絕對路徑，換機/換路徑即失效）。
$ZipPath = Join-Path (Split-Path $Root -Parent) "sql-agent-offline.zip"
$TempDir = Join-Path $env:TEMP "sqlagent_pack_$(Get-Random)"
Copy-Item $Root $TempDir -Recurse
foreach ($drop in ".git", ".venv", "data", ".env") {
    Remove-Item (Join-Path $TempDir $drop) -Recurse -Force -ErrorAction SilentlyContinue
}
Get-ChildItem $TempDir -Recurse -Include "__pycache__", "*.egg-info" |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Compress-Archive -Path "$TempDir\*" -DestinationPath $ZipPath -Force
Remove-Item $TempDir -Recurse -Force

Write-Host ""
Write-Host "完成：$ZipPath"
Write-Host "傳到正式機解壓後，執行 scripts\install_offline.ps1"
