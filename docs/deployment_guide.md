# SQL Agent — 部署指南

> 本文件說明如何在本地開發環境、Docker 容器、及雲端環境中部署 SQL Agent。  
> 雲端架構設計詳見 `docs/system_architecture.md`。

---

## 一、環境需求

| 需求 | 最低版本 | 說明 |
|---|---|---|
| Python | 3.11+ | 後端執行環境 |
| pip | 23+ | 套件安裝 |
| Docker | 24+ | 容器化部署（可選） |
| Docker Compose | 2.20+ | 本地多容器開發（可選） |
| PostgreSQL | 14+ | 目標 DB（審查模式 / DB 匯入功能）|

---

## 二、環境變數

應用程式的所有設定透過 `.env` 管理。複製範本並填入實際值：

```bash
cp .env.example .env
```

`.env.example` 內容：

```dotenv
# ── Pensieve AI API（必填）──────────────────────────────
PENSIEVE_API_TOKEN=<your-token>
PENSIEVE_EMPNO=<your-empno>
PENSIEVE_API_URL=https://pensieve.example.com/api

# ── Flask ──────────────────────────────────────────────
FLASK_ENV=production        # development | production
SECRET_KEY=<random-32-char-string>

# ── 資料目錄（預設為專案根目錄下的 data/）──────────────
# DATA_DIR=/var/app/data
```

> `PENSIEVE_API_TOKEN` 和 `SECRET_KEY` **絕對不能 commit** 至版控。  
> `.env` 已列入 `.gitignore`。

---

## 三、本地開發（無 Docker）

```bash
# 1. 建立並啟動虛擬環境
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 2. 安裝相依套件
pip install -r requirements.txt

# 3. 設定環境變數
cp .env.example .env
# 編輯 .env，填入 PENSIEVE_API_TOKEN 等

# 4. 啟動開發伺服器
python app.py
# 或使用 Flask CLI:
# FLASK_APP=app.py flask run --debug --port 5000

# 5. 開啟瀏覽器
open http://localhost:5000
```

---

## 四、Docker 容器化部署

### 4.1 建立映像

```bash
docker build -t sql-agent:latest .
```

### 4.2 執行容器

```bash
docker run -d \
  --name sql-agent \
  -p 5000:5000 \
  --env-file .env \
  -v $(pwd)/data:/app/data \
  sql-agent:latest
```

- `-v $(pwd)/data:/app/data`：將 session JSON 掛載至 host，避免容器重啟後資料消失
- `--env-file .env`：注入環境變數（不含密鑰的版本可用 `-e KEY=VALUE`）

### 4.3 Docker Compose（本地開發）

```bash
# 啟動
docker compose up -d

# 查看 log
docker compose logs -f app

# 停止並移除
docker compose down
```

`docker-compose.yml` 服務說明：

| 服務 | 說明 |
|---|---|
| `app` | Flask 應用，port 5000 |
| `postgres` | 測試用 PostgreSQL（審查模式 DB 匯入的本地目標）|

---

## 五、Gunicorn 生產設定

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

Dockerfile 的 CMD 使用此指令（見 `Dockerfile`）。

---

## 六、Nginx 反向代理（可選）

在 Load Balancer 或本機用 Nginx 做 HTTPS 終止：

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

## 七、雲端部署（Cloud-agnostic）

參考 `docs/system_architecture.md` 的目標架構。核心步驟（以 Container 服務為例）：

```
1. 推送映像至 Container Registry（ECR / GCR / ACR / DockerHub）
   docker tag sql-agent:latest <registry>/sql-agent:latest
   docker push <registry>/sql-agent:latest

2. 建立 Container Service（ECS Fargate / Cloud Run / Container Apps）
   - Image: <registry>/sql-agent:latest
   - Port: 5000
   - CPU/Memory: 0.5 vCPU / 1 GB（最低）
   - 環境變數從 Secret Manager 注入

3. 設定 Load Balancer（ALB / GLB）→ 指向 Container Service

4. 設定 Health Check：GET /health → 200

5. （可選）設定 Auto Scaling：CPU > 70% 時擴展
```

---

## 八、資料備份

v0.5 使用 JSON 檔案儲存，需定期備份 `data/` 目錄：

```bash
# 手動備份
tar -czf backup-$(date +%Y%m%d).tar.gz data/

# cron 排程（每日凌晨 2 點）
0 2 * * * tar -czf /backups/sql-agent-$(date +\%Y\%m\%d).tar.gz /app/data/
```

v1.0 遷移至 PostgreSQL 後，使用 `pg_dump` 或雲端 DB 快照機制。

---

## 九、版本升級

```bash
# 拉取新版本
git pull origin main

# 更新套件
pip install -r requirements.txt

# 重啟服務
# 本地: ctrl+c && python app.py
# Docker: docker compose restart app
# 生產: 滾動更新（依雲端平台操作）
```
