"""集中式設定：所有環境變數唯一入口（pydantic-settings，啟動即驗證）。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM gateway（OpenAI 相容 Chat Completions）
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_verify: bool = False          # 自簽憑證 gateway 預設不驗證
    llm_timeout: float = 120.0        # read timeout 秒數
    llm_force_profile: str | None = None  # JSON，覆蓋自動偵測的 CapabilityProfile（除錯用）

    # 平台自身資料庫（正式 PostgreSQL；未設定時本機 SQLite）
    database_url: str = "sqlite+aiosqlite:///./data/app.db"

    # 安全
    secret_key: str = "dev-only-secret"
    db_encryption_key: str | None = None  # 32-byte hex，AES-256-GCM 加密連線字串
    admin_token: str | None = None        # change request 審批過渡機制

    # 認證（Phase 7）：false 時所有端點行為與 v0.5 完全相同（匿名、無所有權過濾）。
    auth_enabled: bool = False
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 7
    # 登入端點的速率限制（per-IP 滑動視窗）；一般端點限制較寬鬆，供未來擴充使用。
    auth_rate_limit_login_max: int = 5
    auth_rate_limit_login_window_seconds: float = 60.0
    auth_rate_limit_general_max: int = 60
    auth_rate_limit_general_window_seconds: float = 60.0

    # 伺服器
    host: str = "127.0.0.1"
    debug: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
