# web.config 範本

這是部署 Python + HttpPlatformHandler + Waitress + AD Windows 驗證的完整 `web.config`。
把 `D:\WebServices\my-app` 全部替換成你的實際部署路徑後放到專案根目錄。

---

## 完整範本

```xml
<?xml version="1.0" encoding="UTF-8"?>
<configuration>
  <system.webServer>

    <!-- 1. 停用匿名、啟用 Windows 驗證 -->
    <security>
      <authentication>
        <anonymousAuthentication enabled="false" />
        <windowsAuthentication   enabled="true"  />
      </authentication>
    </security>

    <!-- 2. 將所有請求交給 HttpPlatformHandler -->
    <handlers>
      <add name="httpPlatformHandler"
           path="*" verb="*"
           modules="httpPlatformHandler"
           resourceType="Unspecified" />
    </handlers>

    <!-- 3. HttpPlatformHandler 設定 -->
    <httpPlatform
      processPath="D:\WebServices\my-app\venv\Scripts\waitress-serve.exe"
      arguments="--port=%HTTP_PLATFORM_PORT% --threads=4 wsgi:application"
      stdoutLogEnabled="true"
      stdoutLogFile="D:\WebServices\my-app\logs\python.log"
      startupTimeLimit="60"
      requestTimeout="00:04:00"
      startupRetryCount="3"
      forwardWindowsAuthToken="true">

      <environmentVariables>
        <!-- wsgi.py 所在目錄 -->
        <environmentVariable name="PYTHONPATH"
          value="D:\WebServices\my-app" />
        <!-- 關閉 stdout 緩衝，讓 print() 立即寫入 log -->
        <environmentVariable name="PYTHONUNBUFFERED" value="1" />
        <!-- 繁中 Windows (cp950) 不支援 emoji，強制 UTF-8 避免崩潰 -->
        <environmentVariable name="PYTHONIOENCODING" value="utf-8" />
      </environmentVariables>

    </httpPlatform>
  </system.webServer>
</configuration>
```

---

## 三個必填欄位說明

| 欄位 | 必填原因 |
|------|---------|
| `processPath` | 指向 venv 內的 `waitress-serve.exe`，IIS 用這個啟動 Python 程序 |
| `PYTHONPATH` | 讓 Waitress 找到 `wsgi:application`（`wsgi.py` 所在目錄） |
| `forwardWindowsAuthToken="true"` | 沒有這行，IIS 驗證結果不會傳給 Python，SSO 完全無效 |

---

## 批次替換路徑（PowerShell）

```powershell
$root = 'D:\WebServices\my-app'   # ← 換成你的路徑

(Get-Content "$root\web.config") `
  -replace 'D:\\WebServices\\my-app', $root `
  | Set-Content "$root\web.config"
```

---

## 常見設定問題

### `SERVER_NAME: waitress.invalid`
你直接打到 Waitress 的動態 port（`%HTTP_PLATFORM_PORT%`），繞過了 IIS。
請透過 IIS 站台的 Port（例如 8001）存取，不要直接打 Waitress port。

### log 檔空白
`PYTHONUNBUFFERED=1` 沒設，Python 的 stdout 被緩衝，沒有即時寫入。
或者 `logs\` 目錄不存在、AppPool 帳號沒有寫入權限。

### 啟動後立刻 crash（`startupRetryCount` 用完）
看 `logs\python.log`，通常是 import 錯誤或 venv 套件缺失。
先在命令列手動測試：
```powershell
.\venv\Scripts\python.exe -m waitress --port=9099 wsgi:application
```
