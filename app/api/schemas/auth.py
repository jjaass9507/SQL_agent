"""auth API 新增端點（`GET /auth/me`）的 response model。"""

from pydantic import BaseModel


class MeResponse(BaseModel):
    """GET /auth/me 回應。

    未登入（或 `AUTH_ENABLED=false`）時 `anonymous=True`、`auth_type=None`，
    其餘欄位皆為 None——**永遠回 200、不回 401**（IIS Windows Auth 下 401 會
    觸發瀏覽器原生憑證彈窗，見 python-iis-ad-deploy skill），由前端自行顯示
    登入表單。

    `auth_type`：`manual`（AD 手動登入）／`sso`（IIS Windows SSO）／
    `local`（本地帳密）。
    """

    anonymous: bool = False
    email: str | None = None
    display_name: str | None = None
    role: str | None = None
    auth_source: str | None = None
    auth_type: str | None = None
