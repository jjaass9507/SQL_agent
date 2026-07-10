# SQL Agent — 部署指南

> 本文件說明如何在本地開發環境及生產伺服器上部署 SQL Agent。  
> 雲端架構設計詳見 `docs/system_architecture.md`。

---

## 一、環境需求

| 需求 | 最低版本 | 說明 |
|---|---|---|
| Python | 3.11+ | 後端執行環境 |
| pip | 23+ | 套件安裝 |
| PostgreSQL | 14+ | 目標 DB（審查模式 / DB 匯入功能）|

---

## 二、環境變數

應用程式的所有設定透過 `.env` 管理。複製範本並填入實際值：

```bash
cp .env.example .env
```

`.env.example` 內容：

```dotenv
# ── LLM API（OpenAI 相容 Chat Completions，必填）──────────
LLM_BASE_URL=https://your-llm-gateway.example.com/v1
LLM_API_KEY=your_api_key_here
LLM_MODEL=your_model_here
LLM_VERIFY=false

# ── Flask ──────────────────────────────────────────────
SECRET_KEY=<random-32-char-string>   # 非 debug 模式下仍是預設值會啟動報錯
FLASK_DEBUG=false                    # 1/true 開啟 debug 模式，預設關閉
HOST=127.0.0.1                       # python app.py 綁定位址，對外可設 0.0.0.0

# ── 資料目錄（預設為專案根目錄下的 data/）──────────────
# DATA_DIR=/var/app/data
```

> `LLM_API_KEY` 和 `SECRET_KEY` **絕對不能 commit** 至版控。  
> `.env` 已列入 `.gitignore`。

---

## 三、本地開發

```bash
# 1. 建立並啟動虛擬環境
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 2. 安裝相依套件
pip install -r requirements.txt

# 3. 設定環境變數
cp .env.example .env
# 編輯 .env，填入 LLM_API_KEY 等

# 4. 啟動開發伺服器
python app.py
# 或使用 Flask CLI:
# FLASK_APP=app.py flask run --debug --port 5000

# 5. 開啟瀏覽器
open http://localhost:5000
```

---

## 四、Gunicorn 生產部署

開發模式下使用 Flask dev server，**生產環境必須改用 Gunicorn**：

```bash
pip install gunicorn

gunicorn \
  --workers 4 \
  --threads 2 \
  --timeout 120 \
  --bind 0.0.0.0:5000 \
  --access-logfile - \
  --error-logfile - \
  app:app
```

建議的 worker 數量：`(2 × CPU cores) + 1`。  
`--timeout 120` 對應 AI API 最大回應時間（P95 < 30s，留有緩衝）。

### systemd 服務（可選）

在 Linux 伺服器上可用 systemd 管理程序：

```ini
# /etc/systemd/system/sql-agent.service
[Unit]
Description=SQL Agent
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/sql-agent
EnvironmentFile=/opt/sql-agent/.env
ExecStart=/opt/sql-agent/.venv/bin/gunicorn \
    --workers 4 --threads 2 --timeout 120 \
    --bind 127.0.0.1:5000 \
    --access-logfile /var/log/sql-agent/access.log \
    --error-logfile /var/log/sql-agent/error.log \
    app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable sql-agent
sudo systemctl start sql-agent
sudo systemctl status sql-agent
```

---

## 五、Nginx 反向代理（可選）

用 Nginx 做 HTTPS 終止：

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate     /etc/nginx/ssl/cert.pem;
    ssl_certificate_key /etc/nginx/ssl/key.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_read_timeout 120s;
    }
}
```

---

## 六、資料備份

v0.5 使用 JSON 檔案儲存，需定期備份 `data/` 目錄：

```bash
# 手動備份
tar -czf backup-$(date +%Y%m%d).tar.gz data/

# cron 排程（每日凌晨 2 點）
0 2 * * * tar -czf /backups/sql-agent-$(date +\%Y\%m\%d).tar.gz /opt/sql-agent/data/
```

v1.0 遷移至 PostgreSQL 後，使用 `pg_dump` 或雲端 DB 快照機制。

---

## 七、版本升級

```bash
# 拉取新版本
git pull origin main

# 更新套件
pip install -r requirements.txt

# 重啟服務
sudo systemctl restart sql-agent
# 或直接重啟 Gunicorn 程序
```
