<#
.SYNOPSIS
    SQL Agent v2 重新部署腳本（Windows）。

.DESCRIPTION
    兩種模式：

      -Mode Direct  直接以 venv 的 uvicorn 跑起來（測試／驗收用，目前使用中）
      -Mode IIS     正式部署：更新原始碼與相依套件後 recycle 應用程式集區
                    （對外由 IIS + Windows Authentication 服務，見 docs/deployment.md §4）

    流程一致，只有「怎麼停、怎麼起」不同：

      前置檢查 → 停服務 → 更新原始碼 → 更新套件 → 備份資料庫
      → alembic upgrade head → 起服務 → 健康檢查

    遷移失敗會自動 alembic downgrade 回原本的版本，並印出備份還原指令與
    回到前一版原始碼的 git 指令。健康檢查失敗不會自動回滾（資料庫已經是新版），
    只會停在那裡並指出 log 位置——這種情況要人看過再決定。

.EXAMPLE
    # 先看它打算做什麼，什麼都不會改
    .\scripts\deploy.ps1 -Mode Direct -DryRun

.EXAMPLE
    # 測試環境：直接跑起來
    .\scripts\deploy.ps1 -Mode Direct

.EXAMPLE
    # 正式環境：IIS，離線安裝套件
    .\scripts\deploy.ps1 -Mode IIS -AppPool SqlAgent -WheelDir C:\offline_wheels

.NOTES
    需要在專案根目錄執行（腳本會自己切過去）。
    IIS 模式需要系統管理員權限（操作應用程式集區）。
#>

#Requires -Version 5.1

[CmdletBinding()]
param(
    [ValidateSet('Direct', 'IIS')]
    [string]$Mode = 'Direct',

    [string]$Branch = 'claude/sql-agent-features-qzpyem',
    [switch]$SkipGit,

    [string]$WheelDir,
    [switch]$SkipDeps,

    [string]$BackupDir = 'backups',
    [switch]$SkipBackup,

    # IIS 模式
    [string]$AppPool = 'SqlAgent',

    # Direct 模式
    [int]$Port = 8000,

    [int]$HealthTimeoutSeconds = 60,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# PS 7.3+ 會讓原生指令的非零離開碼自動丟例外；本腳本自己檢查 $LASTEXITCODE，
# 關掉它才能給出自己的中文訊息。5.1 沒有這個變數，用 Test-Path 判斷。
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $false
}
# $IsWindows 是 PS 6 才有的自動變數；5.1 沒有，而 5.1 本來就只跑在 Windows 上。
# StrictMode 下直接讀未定義變數會炸，所以要先確認它存在。
$IsWindowsOS = if (Test-Path variable:IsWindows) { $IsWindows } else { $true }

$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root 'venv\Scripts\python.exe'
$LogDir = Join-Path $Root 'logs'
$PidFile = Join-Path $LogDir 'uvicorn.pid'

# venv 內的 .exe 進入點內嵌了建立當下的絕對路徑，venv 被搬動過就會噴
# "Fatal error in launcher"（見 docs/deployment.md §4-4）。一律用 python -m
# 呼叫，繞開所有 .exe launcher。
# Windows PowerShell 5.1 會把原生指令寫到 stderr 的內容轉成 ErrorRecord，
# 搭配 $ErrorActionPreference='Stop' 就變成終止性錯誤——alembic 只是把 INFO log
# 寫到 stderr（例如「Context impl SQLiteImpl.」），完全不是失敗，卻會讓部署中止。
# 因此執行原生指令期間一律把 EAP 降成 Continue，成功與否只看 $LASTEXITCODE。
function Invoke-Native {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$Label,
        [switch]$AllowFail
    )
    if (-not $Label) { $Label = "$FilePath $($Arguments -join ' ')" }
    Write-Step $Label
    if ($DryRun) { return }

    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $FilePath @Arguments }
    finally { $ErrorActionPreference = $previous }

    if (-not $AllowFail -and $LASTEXITCODE -ne 0) { throw "$Label 失敗（exit $LASTEXITCODE）" }
}

# 需要讀取輸出時用這個。2>&1 會把 stderr 併進管線（5.1 是 ErrorRecord、7.x 也是），
# 一律濾掉，只回傳標準輸出的字串陣列。
function Invoke-NativeCapture {
    param([Parameter(Mandatory)][string]$FilePath, [string[]]$Arguments = @())
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $raw = & $FilePath @Arguments 2>&1 }
    finally { $ErrorActionPreference = $previous }
    return @($raw |
        Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } |
        ForEach-Object { "$_" })
}

function Invoke-Py {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$PyArgs)
    Invoke-Native -FilePath $VenvPython -Arguments (@('-m') + $PyArgs) -Label "python -m $($PyArgs -join ' ')"
}

function Write-Step { param([string]$Text) Write-Host "  → $Text" -ForegroundColor DarkGray }
function Write-Section { param([string]$Text) Write-Host "`n[$Text]" -ForegroundColor Cyan }
function Write-Ok { param([string]$Text) Write-Host "  ✓ $Text" -ForegroundColor Green }
function Write-Warn { param([string]$Text) Write-Host "  ! $Text" -ForegroundColor Yellow }

# ---------------------------------------------------------------- 前置檢查

function Test-Preflight {
    Write-Section '前置檢查'

    if (-not (Test-Path $VenvPython)) {
        throw "找不到 venv：$VenvPython`n" +
              "  請在專案最終路徑上原地建立（不要在別處建好再搬）：`n" +
              "    python -m venv $(Join-Path $Root 'venv')"
    }
    Write-Ok "venv：$VenvPython"

    $envFile = Join-Path $Root '.env'
    if (-not (Test-Path $envFile)) {
        throw "找不到 .env（可從 .env.example 複製後填值）：$envFile"
    }

    $settings = @{}
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$') {
            $settings[$Matches[1]] = $Matches[2].Trim().Trim('"')
        }
    }
    function Get-EnvValue {
        param([string]$Key)
        if ($settings.ContainsKey($Key)) { return $settings[$Key] }
        return ''
    }

    # 缺這個 → 業務資料庫連線字串解不開，設定頁整組壞掉
    if (-not (Get-EnvValue 'DB_ENCRYPTION_KEY')) {
        throw "DB_ENCRYPTION_KEY 未設定。產生方式：`n" +
              "    python -c `"import os; print(os.urandom(32).hex())`""
    }
    Write-Ok 'DB_ENCRYPTION_KEY 已設定'

    if (-not (Get-EnvValue 'SECRET_KEY') -or (Get-EnvValue 'SECRET_KEY') -eq 'dev-only-secret') {
        Write-Warn 'SECRET_KEY 仍是預設值，正式環境務必更換'
    }
    # 未設定時審批端點一律 403（fail-closed），設定頁會無法新增業務資料庫
    if (-not (Get-EnvValue 'ADMIN_TOKEN')) {
        Write-Warn 'ADMIN_TOKEN 未設定：變更審批與業務資料庫設定端點會回 403'
    }

    if ($Mode -eq 'IIS') {
        if ((Get-EnvValue 'AUTH_ENABLED') -ne 'true') {
            Write-Warn 'AUTH_ENABLED 不是 true——IIS + AD SSO 需要它才會驗證身分'
        }
        if ((Get-EnvValue 'AD_ENABLED') -ne 'true' -or (Get-EnvValue 'AD_SSO_ENABLED') -ne 'true') {
            Write-Warn 'AD_ENABLED / AD_SSO_ENABLED 不是 true，SSO 不會生效'
        }
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = [Security.Principal.WindowsPrincipal]::new($identity)
        if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
            throw 'IIS 模式需要系統管理員權限（操作應用程式集區）。請以管理員身分重新執行。'
        }
        Import-Module WebAdministration -ErrorAction Stop
        if (-not (Test-Path "IIS:\AppPools\$AppPool")) {
            throw "找不到應用程式集區：$AppPool（可用 -AppPool 指定正確名稱）"
        }
        Write-Ok "IIS 應用程式集區：$AppPool"
    }

    # 0 = 已最新、1 = 有待跑的遷移，都是正常；2 才是連不上或設定有問題
    Invoke-Native -FilePath $VenvPython `
        -Arguments @((Join-Path $Root 'scripts\deploy_db.py'), 'check') `
        -Label 'scripts\deploy_db.py check' -AllowFail
    if (-not $DryRun -and $LASTEXITCODE -eq 2) { throw '資料庫檢查失敗，見上方訊息' }
}

# ---------------------------------------------------------------- 起停服務

function Stop-App {
    Write-Section '停止服務'
    if ($Mode -eq 'IIS') {
        Write-Step "Stop-WebAppPool $AppPool"
        if (-not $DryRun) {
            if ((Get-WebAppPoolState -Name $AppPool).Value -ne 'Stopped') {
                Stop-WebAppPool -Name $AppPool
            }
            $deadline = (Get-Date).AddSeconds(30)
            while ((Get-WebAppPoolState -Name $AppPool).Value -ne 'Stopped') {
                if ((Get-Date) -gt $deadline) { throw "應用程式集區 $AppPool 在 30 秒內沒有停下來" }
                Start-Sleep -Milliseconds 500
            }
            Write-Ok "應用程式集區已停止"
        }
        return
    }

    # Direct：先看 pid 檔，再退回用 port 找（pid 檔可能因為非正常關閉而過期）
    $target = $null
    if ((Test-Path $PidFile) -and -not $DryRun) {
        $saved = (Get-Content $PidFile -Raw).Trim()
        $target = Get-Process -Id ([int]$saved) -ErrorAction SilentlyContinue
    }
    # pid 檔不可靠時（非正常關閉留下舊 pid）改用 port 反查。Get-NetTCPConnection
    # 只有 Windows 有，先確認存在再用，否則就只靠 pid 檔。
    if (-not $target -and -not $DryRun -and (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
                Select-Object -First 1
        if ($conn) { $target = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue }
    }
    if ($target) {
        Write-Step "停止 PID $($target.Id)（$($target.ProcessName)）"
        if (-not $DryRun) {
            Stop-Process -Id $target.Id -Force
            Start-Sleep -Seconds 1
        }
        Write-Ok '舊的 uvicorn 已停止'
    }
    else {
        Write-Step "port $Port 上沒有執行中的服務，跳過"
    }
    if ((Test-Path $PidFile) -and -not $DryRun) { Remove-Item $PidFile -Force }
}

function Start-App {
    Write-Section '啟動服務'
    if ($Mode -eq 'IIS') {
        Write-Step "Start-WebAppPool $AppPool"
        if (-not $DryRun) {
            Start-WebAppPool -Name $AppPool
            Write-Ok '應用程式集區已啟動（IIS 會在第一個請求進來時拉起 uvicorn）'
        }
        return
    }

    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $stdout = Join-Path $LogDir 'uvicorn.out.log'
    $stderr = Join-Path $LogDir 'uvicorn.err.log'
    Write-Step "uvicorn app.main:app --host 127.0.0.1 --port $Port"
    if ($DryRun) { return }

    # 繁中 Windows 預設 cp950，遇到 emoji／特殊字元會讓行程直接崩潰；
    # 沒關 buffer 的話 log 會一片空白（見 docs/deployment.md §4-2）。
    $env:PYTHONUNBUFFERED = '1'
    $env:PYTHONIOENCODING = 'utf-8'

    $startArgs = @{
        FilePath               = $VenvPython
        ArgumentList           = @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$Port")
        WorkingDirectory       = $Root
        PassThru               = $true
        RedirectStandardOutput = $stdout
        RedirectStandardError  = $stderr
    }
    # -WindowStyle 只有 Windows 版 PowerShell 支援；沒有它 python.exe 會彈出主控台視窗。
    if ($IsWindowsOS) { $startArgs['WindowStyle'] = 'Hidden' }
    $proc = Start-Process @startArgs
    $proc.Id | Set-Content $PidFile
    Write-Ok "uvicorn 已啟動（PID $($proc.Id)），log：$stdout"
}

function Test-Health {
    Write-Section '健康檢查'
    $url = "http://127.0.0.1:$Port/healthz"
    Write-Step "GET $url（最多等 $HealthTimeoutSeconds 秒）"
    if ($DryRun) { return }

    $deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-RestMethod -Uri $url -TimeoutSec 5
            if ($resp.status -eq 'ok') {
                Write-Ok '健康檢查通過'
                return
            }
        }
        catch { Start-Sleep -Seconds 2 }
    }
    throw "健康檢查在 $HealthTimeoutSeconds 秒內沒有通過。`n" +
          "  資料庫已經是新版，不會自動回滾——請先看 log 再決定：`n" +
          "    $(Join-Path $LogDir 'uvicorn.err.log')"
}

# ---------------------------------------------------------------- 主流程

Push-Location $Root
$previousCommit = $null
$previousRevision = $null
$backupHint = '（本次未備份）'

try {
    Write-Host "SQL Agent v2 重新部署（模式：$Mode$(if ($DryRun) { '，DryRun' })）" -ForegroundColor White
    Write-Host "  PowerShell $($PSVersionTable.PSVersion)" -ForegroundColor DarkGray

    Test-Preflight
    Stop-App

    if (-not $SkipGit) {
        Write-Section '更新原始碼'
        if (-not (Test-Path (Join-Path $Root '.git'))) {
            throw "這個目錄不是 git repo，無法用 -Branch 更新原始碼。`n" +
                  "  若原始碼是用複製／解壓縮的方式部署，請加 -SkipGit 跳過這一步。"
        }
        $previousCommit = @(Invoke-NativeCapture -FilePath 'git' -Arguments @('rev-parse', 'HEAD'))[0]
        Write-Step "目前 commit：$previousCommit"
        # git 會把進度訊息寫到 stderr，一律走 Invoke-Native（見該函式的註解）
        Invoke-Native -FilePath 'git' -Arguments @('fetch', 'origin', $Branch)
        Invoke-Native -FilePath 'git' -Arguments @('checkout', $Branch)
        Invoke-Native -FilePath 'git' -Arguments @('pull', '--ff-only', 'origin', $Branch) `
            -Label "git pull --ff-only origin $Branch"
        if (-not $DryRun) {
            $short = @(Invoke-NativeCapture -FilePath 'git' -Arguments @('rev-parse', '--short', 'HEAD'))[0]
            Write-Ok "已更新到 $short"
        }
    }

    if (-not $SkipDeps) {
        Write-Section '更新相依套件'
        if ($WheelDir) {
            Invoke-Py 'pip' 'install' '--no-index' "--find-links=$WheelDir" '-e' '.'
        }
        else {
            Invoke-Py 'pip' 'install' '-e' '.'
        }
        if (-not $DryRun) { Write-Ok '相依套件已更新' }
    }

    if (-not $SkipBackup) {
        Write-Section '備份資料庫'
        Write-Step "scripts\deploy_db.py backup --out-dir $BackupDir"
        if (-not $DryRun) {
            $output = Invoke-NativeCapture -FilePath $VenvPython `
                -Arguments @((Join-Path $Root 'scripts\deploy_db.py'), 'backup', '--out-dir', $BackupDir)
            if ($LASTEXITCODE -ne 0) {
                $output | ForEach-Object { Write-Host "    $_" }
                throw '備份失敗，中止部署'
            }
            $output | ForEach-Object { Write-Host "    $_" }
            $backupHint = ($output | Where-Object { $_ -match '還原指令' }) -join ''
            Write-Ok '備份完成'
        }
    }
    else {
        Write-Warn '已指定 -SkipBackup，跳過備份'
    }

    Write-Section '資料庫遷移'
    if (-not $DryRun) {
        $previousRevision = (Invoke-NativeCapture -FilePath $VenvPython -Arguments @('-m', 'alembic', 'current') |
            Select-String -Pattern '^[0-9a-f]+' | ForEach-Object { $_.Matches[0].Value }) -join ''
        Write-Step "目前版本：$(if ($previousRevision) { $previousRevision } else { '(空的資料庫)' })"
    }
    try {
        Invoke-Py 'alembic' 'upgrade' 'head'
        if (-not $DryRun) { Write-Ok '遷移完成' }
    }
    catch {
        Write-Host "`n遷移失敗，正在回滾……" -ForegroundColor Red
        if ($previousRevision) {
            Invoke-Native -FilePath $VenvPython `
                -Arguments @('-m', 'alembic', 'downgrade', $previousRevision) `
                -Label "python -m alembic downgrade $previousRevision" -AllowFail
            if ($LASTEXITCODE -eq 0) {
                Write-Ok "資料庫已回到 $previousRevision"
            }
            else {
                Write-Warn "自動回滾也失敗了，請用備份還原：`n    $backupHint"
            }
        }
        if ($previousCommit) {
            Write-Warn "要回到前一版原始碼：`n    git checkout $previousCommit"
        }
        throw
    }

    Start-App
    Test-Health

    if ($DryRun) {
        Write-Host "`nDryRun 結束：以上是實際執行時會做的事，本次沒有任何變更。" -ForegroundColor Yellow
        Pop-Location
        exit 0
    }
    Write-Host "`n部署完成。" -ForegroundColor Green
    if ($Mode -eq 'Direct') {
        Write-Host "  服務位址：http://127.0.0.1:$Port/" -ForegroundColor Green
    }
    else {
        Write-Host "  對外由 IIS 服務，請以站台實際網址確認。" -ForegroundColor Green
    }
}
catch {
    Write-Host "`n部署中止：$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    Pop-Location
}
