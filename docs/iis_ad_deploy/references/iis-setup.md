# IIS 設定參考

PowerShell 指令與 IIS 管理員 UI 操作並列，擇一使用。

---

## Step 1：安裝 IIS 功能 + HttpPlatformHandler

### PowerShell
```powershell
# 以系統管理員身分執行
Install-WindowsFeature `
  -Name Web-Server, Web-Windows-Auth, Web-CGI `
  -IncludeManagementTools
```
> `Web-CGI` 是 HttpPlatformHandler 的前置需求，不能省略。

### IIS UI
1. 伺服器管理員 → 新增角色及功能
2. 網頁伺服器 (IIS) → 角色服務
3. 勾選：**Windows 驗證** + **CGI**
4. 完成後重啟

### 安裝 HttpPlatformHandler v2.0 MSI
下載：https://www.iis.net/downloads/microsoft/httpplatformhandler

> IIS 不內建，必須單獨下載 MSI 安裝。

驗證安裝（有輸出 = 成功）：
```powershell
Get-WebGlobalModule -Name "httpPlatformHandler*"
```

---

## Step 2：安裝 Python（全域安裝）

安裝時必須勾選：
- ✅ **Install for all users** → 安裝到 `C:\Program Files\PythonXX`（**必選**）
- ✅ **Add Python to PATH**（**必選**）

> 若裝在 `C:\Users\...\AppData\`，IIS AppPool 帳號無法存取，Python 程序永遠無法啟動。

確認路徑：
```powershell
where.exe python
# 正確：C:\Program Files\PythonXX\python.exe
# 錯誤：C:\Users\...\AppData\... → 須重裝
```

---

## Step 3：建立 venv + 安裝套件

```powershell
cd D:\WebServices\my-app

# 每台機器、每個路徑都要重建（venv 不可搬移）
python -m venv venv

# 離線安裝（from wheels/）
.\venv\Scripts\pip install `
  --no-index `
  --find-links=wheels `
  -r requirements.txt

# 驗證
.\venv\Scripts\pip list

# 建立 logs 目錄
New-Item -ItemType Directory -Force ".\logs"
```

---

## Step 4：建立 AppPool + 網站

### PowerShell
```powershell
Import-Module WebAdministration

$name = 'my-app'            # ← 換成你的服務名稱
$pool = "Pool-$name"
$root = 'D:\WebServices\my-app'   # ← 換成實際路徑
$port = 8001                # ← 換成實際 port

# AppPool（No Managed Code + AlwaysRunning）
New-WebAppPool -Name $pool
Set-ItemProperty "IIS:\AppPools\$pool" managedRuntimeVersion ""
Set-ItemProperty "IIS:\AppPools\$pool" startMode AlwaysRunning

# 網站
New-Website -Name $name `
  -PhysicalPath $root `
  -ApplicationPool $pool `
  -Port $port -Force
```

### IIS UI
1. 左欄「應用程式集區」→ 右側「新增應用程式集區」
2. 名稱填 `Pool-my-app`
3. **.NET CLR 版本**選「**沒有 Managed 程式碼**」
4. 「進階設定」→「啟動模式」改為 `AlwaysRunning`
5. 左欄「網站」→ 右側「新增網站」
6. 填入：名稱、實體路徑（`D:\WebServices\my-app`）
7. 應用程式集區選 `Pool-my-app`
8. 繫結 → 連接埠填 `8001`

> 若看不到「.NET CLR 版本」下拉，關閉後重新開啟 IIS 管理員。

---

## Step 5：設定 Windows 驗證

### PowerShell
```powershell
$sp = "IIS:\Sites\$name"

# 停用匿名（必須關閉）
Set-WebConfigurationProperty `
  -Filter "system.webServer/security/authentication/anonymousAuthentication" `
  -Name enabled -Value $false -PSPath $sp

# 啟用 Windows 驗證
Set-WebConfigurationProperty `
  -Filter "system.webServer/security/authentication/windowsAuthentication" `
  -Name enabled -Value $true -PSPath $sp
```

### IIS UI
1. 點選網站 → 中間面板「**驗證**」圖示（雙擊）
2. 「匿名驗證」→ **停用**
3. 「Windows 驗證」→ **啟用**
4. Windows 驗證「提供者」→ 確認有 `Negotiate` 和 `NTLM`

---

## Step 6：設定目錄權限

### PowerShell
```powershell
function Grant($path, $pool, $right) {
  $acl  = Get-Acl $path
  $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    "IIS AppPool\$pool", $right,
    "ContainerInherit,ObjectInherit", "None", "Allow"
  )
  $acl.SetAccessRule($rule)
  Set-Acl $path $acl
}

Grant $root         $pool "ReadAndExecute"   # 主目錄
Grant "$root\logs"  $pool "Modify"           # logs 子目錄（Python 需要寫入）
```

### IIS UI（以主目錄為例）
1. 資料夾右鍵 → 內容 → 安全性 → 編輯 → 新增
2. 輸入物件名稱：`IIS AppPool\Pool-my-app`（換成你的 AppPool 名稱）
3. 「檢查名稱」確認後按確定
4. 主目錄：給「**讀取和執行**」
5. `logs\` 子目錄：另外給「**修改**」

---

## Step 7：重啟並驗證

```powershell
iisreset /restart

# 即時看 log
Get-Content "D:\WebServices\my-app\logs\python.log" -Tail 20 -Wait
```

---

## 多服務隔離

一機多服務時，每個服務：
- 獨立部署目錄（各自的 `venv/`、`.env`、`web.config`）
- 獨立 AppPool（`Pool-service-a`、`Pool-service-b`……）
- 不同 Port（`8001`、`8002`……）或用 IIS 子應用程式（`/hr`、`/finance`……）

新增服務快速腳本：
```powershell
# 修改變數後執行即可
$name = 'service-b'
$port = 8002
$root = "D:\WebServices\$name"
# … 重複 Step 4–6 的指令
```
