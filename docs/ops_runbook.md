# SQL Agent — 維運 Runbook

> 本文件供維運工程師在日常維護、故障排查、及緊急事件時參考。  
> 假設已依 `docs/deployment_guide.md` 完成部署。

---

## 一、服務確認

### 基本健康檢查

```bash
# HTTP health check
curl http://localhost:5000/health
# 預期: {"status": "ok", "version": "0.5.0"}

# Docker 容器狀態
docker ps --filter name=sql-agent

# 查看應用 log（近 100 行）
docker logs --tail 100 sql-agent

# 追蹤即時 log
docker logs -f sql-agent
```

### 服務指標確認

| 項目 | 正常範圍 | 確認指令 |
|---|---|---|
| 容器狀態 | Up | `docker ps` |
| Health endpoint | HTTP 200 | `curl /health` |
| 記憶體用量 | < 500 MB | `docker stats sql-agent` |
| CPU 用量（閒置）| < 5% | `docker stats sql-agent` |
| Data 目錄大小 | 視 session 數量 | `du -sh data/` |

---

## 二、常見問題排查

### P1：服務無回應 / 連線失敗

**症狀**：瀏覽器顯示「無法連線」或 curl 超時

**排查步驟**：
```bash
# 確認容器是否在執行
docker ps --filter name=sql-agent

# 若容器停止，查看退出原因
docker logs sql-agent --tail 50

# 重啟容器
docker restart sql-agent

# 若重啟後仍失敗，查看是否有 port 衝突
lsof -i :5000
```

**常見原因**：
- 容器 OOM 被 kill（記憶體不足）
- Port 5000 被其他程序占用
- `.env` 缺少必要環境變數導致啟動失敗

---

### P2：AI 回應失敗（對話頁顯示錯誤）

**症狀**：使用者送出訊息後，對話頁顯示「⚠ 伺服器錯誤」

**排查步驟**：
```bash
# 查看 application log 中的 ERROR
docker logs sql-agent 2>&1 | grep '"level":"ERROR"'

# 確認 Pensieve API 連線
curl -H "Authorization: Bearer $PENSIEVE_API_TOKEN" \
  $PENSIEVE_API_URL/health
```

**常見原因**：
- `PENSIEVE_API_TOKEN` 過期或錯誤
- Pensieve API 服務中斷
- 網路防火牆封鎖出站連線

---

### P3：文件產出卡在「產出中」

**症狀**：文件頁進度卡長時間顯示 loading，不更新

**排查步驟**：
```bash
# 確認 session 的 generation_status
cat data/<session_id>.json | python -m json.tool | grep generation_status

# 查看 worker log
docker logs sql-agent 2>&1 | grep "writer failed"
```

**常見原因**：
- Writer 呼叫 LLM API 超時（單次最長 300s）
- LLM API 回傳空內容（`"Writer 回傳空內容"` 錯誤）
- 記憶體不足導致 Thread 被 kill

**處理方式**：
- 若超過 10 分鐘未完成，建議使用者返回對話頁重新確認
- 若問題持續，重啟容器（現有進行中的 session 會需要重試）

---

### P4：DB 匯入失敗

**症狀**：建立 session 時顯示 DB 連線錯誤

**確認步驟**：
1. 確認連線字串格式：`postgresql://user:pass@host:5432/dbname`
2. 確認應用容器可網路連通目標 DB（`host` 可解析且 5432 開通）
3. 確認 DB 使用者有 `SELECT` 權限：
   ```sql
   GRANT SELECT ON ALL TABLES IN SCHEMA public TO <user>;
   GRANT SELECT ON information_schema.columns TO <user>;
   ```

---

### P5：Session JSON 損毀

**症狀**：存取特定 session 回傳 500，或首頁不顯示該 session

**排查與修復**：
```bash
# 確認 JSON 格式
python -m json.tool data/<session_id>.json

# 若損毀，查看是否有備份
ls -la backups/

# 若無備份，刪除損毀檔案（使用者需重建此 session）
rm data/<session_id>.json
```

---

## 三、日常維護

### 定期備份（每日）

```bash
#!/bin/bash
# /etc/cron.daily/sql-agent-backup

BACKUP_DIR=/backups/sql-agent
DATE=$(date +%Y%m%d)

mkdir -p $BACKUP_DIR
tar -czf $BACKUP_DIR/sessions-$DATE.tar.gz /app/data/

# 保留最近 30 天備份
find $BACKUP_DIR -name "sessions-*.tar.gz" -mtime +30 -delete
```

### 清理舊 Session（可選）

若 session 數量過多影響效能：

```bash
# 查看 data/ 目錄下檔案數量
ls data/*.json | wc -l

# 列出 30 天前的 session（不自動刪除，需人工確認）
find data/ -name "*.json" -mtime +30 -ls
```

### 套件安全更新

```bash
# 查看有安全漏洞的套件
pip audit

# 更新指定套件
pip install --upgrade <package>
pip freeze > requirements.txt
```

---

## 四、Log 分析

Log 為 JSON 格式（`_JsonFormatter`，v1.0 實作後），可用 `jq` 解析：

```bash
# 過濾 ERROR 等級
docker logs sql-agent 2>&1 | grep '"level":"ERROR"' | jq .

# 查看某 session 的所有 log
docker logs sql-agent 2>&1 | jq 'select(.session_id == "<uuid>")'

# 統計錯誤數（最近 1 小時）
docker logs sql-agent --since 1h 2>&1 | grep '"level":"ERROR"' | wc -l
```

> v0.5 使用 `print()` 輸出，log 為純文字格式；v1.0 改為 JSON 後才能使用上述 `jq` 指令。

---

## 五、緊急回滾

若新版本部署後發現嚴重問題：

```bash
# 方法 1：換回前一個映像 tag
docker stop sql-agent
docker rm sql-agent
docker run -d --name sql-agent \
  -p 5000:5000 --env-file .env \
  -v $(pwd)/data:/app/data \
  sql-agent:<previous-tag>

# 方法 2：Git 回滾後重新建置
git checkout <previous-commit>
docker build -t sql-agent:rollback .
# 然後同上執行
```

回滾後確認：
```bash
curl http://localhost:5000/health
# 確認 version 欄位回到舊版本號
```

---

## 六、聯絡與升級路徑

| 問題等級 | 描述 | 初步處理 | 升級對象 |
|---|---|---|---|
| P1 | 服務完全無法使用 | 重啟容器；通知使用者 | 後端開發者（立即）|
| P2 | 功能降級（部分使用者受影響）| 查 log；確認外部服務 | 後端開發者（1 小時內）|
| P3 | 效能問題（回應變慢）| 查資源用量；必要時重啟 | 後端開發者（次日）|
| P4 | 資料問題（session 損毀）| 從備份還原 | 後端開發者 + PO（確認範圍）|
