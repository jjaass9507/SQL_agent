#!/usr/bin/env bash
# SQL Agent v2 本機啟動 + 驅動 smoke。從乾淨 checkout 把 app 跑起來、curl 打幾個
# 關鍵端點確認活著，最後收乾淨。這是 agent 驅動這個 web app 的主要 handle。
#
# 用法（在專案根目錄）：
#   bash .claude/skills/run-sql-agent/smoke.sh
#
# 退出碼 0 = 全部端點回 200；非 0 = 有端點掛掉（會印出 uvicorn log）。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT"
PORT="${PORT:-8000}"
BASE="http://127.0.0.1:${PORT}"
LOG="$(mktemp)"

fail() { echo "FAIL: $*"; echo "--- uvicorn log ---"; cat "$LOG"; cleanup; exit 1; }
cleanup() { [ -n "${PID:-}" ] && kill "$PID" 2>/dev/null; wait "${PID:-}" 2>/dev/null; }
trap cleanup EXIT

# 1. sqlite 需要 data/ 目錄先存在（在 .gitignore，乾淨 checkout 沒有它）
mkdir -p data

# 2. 建表（冪等）。指向與啟動同一個 DATABASE_URL（此處都用預設 sqlite）
echo "==> alembic upgrade head"
alembic upgrade head >>"$LOG" 2>&1 || fail "alembic upgrade 失敗"

# 3. 背景啟動 uvicorn（單一行程，見 Gotchas：不可多 worker）
echo "==> 啟動 uvicorn on :${PORT}"
uvicorn app.main:app --host 127.0.0.1 --port "$PORT" >>"$LOG" 2>&1 &
PID=$!

# 4. 等 /healthz 起來（最多 ~15s）
for i in $(seq 1 30); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/healthz" 2>/dev/null || true)
    [ "$code" = "200" ] && break
    kill -0 "$PID" 2>/dev/null || fail "uvicorn 提早結束"
    sleep 0.5
done
[ "$code" = "200" ] || fail "/healthz 未在時限內回 200"

# 5. 驅動關鍵端點，全部要 200
check() {
    local path="$1" code
    code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE$path")
    if [ "$code" = "200" ]; then echo "  OK   $path [$code]"; else fail "$path 回 $code（預期 200）"; fi
}
echo "==> 驅動端點"
check /healthz                 # ops 健康檢查
check /                        # 前端首頁（HTML）
check /docs                    # OpenAPI 文件
check /api/v1/settings         # 平台設定（sqlite backend）
check /api/v1/llm/health       # LLM 健康檢查（未設 gateway 時 ok:false 仍回 200）

echo ""
echo "PASS：app 可啟動並回應。LLM 端點 ok:false 屬正常（未設 gateway）。"
