# syntax=docker/dockerfile:1

# ---- builder：安裝依賴（含 PostgreSQL driver）----
FROM python:3.11-slim AS builder

WORKDIR /build

RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

COPY pyproject.toml ./
COPY app ./app

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[postgres]"

# ---- runtime：精簡映像，非 root 執行 ----
FROM python:3.11-slim AS runtime

RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --create-home app

WORKDIR /app

COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH"

# 直接複製原始碼（而非只依賴 builder 階段 `pip install .` 裝進 /venv 的版本）：
# pyproject.toml 未宣告 package-data，wheel 打包不保證含 templates/static/
# prompts 等非 .py 資源；uvicorn 執行時 CWD 會被加進 sys.path 最前面，
# 這裡的 /app/app 會優先於 site-packages 版本被匯入，確保靜態資源齊全。
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

RUN mkdir -p /app/data && chown -R app:app /app

USER app

ENV HOST=0.0.0.0

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
