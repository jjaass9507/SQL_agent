"""auth API 新增端點（`GET /auth/me`）的 response model。"""

from pydantic import BaseModel


class MeResponse(BaseModel):
    """GET /auth/me 回應。

    `AUTH_ENABLED=false` 時只有 `anonymous=True`，其餘欄位皆為 None
    （見 app/api/deps.py 的 `get_current_user`：此時恆回傳 None，不視為未登入）。
    """

    anonymous: bool = False
    email: str | None = None
    display_name: str | None = None
    role: str | None = None
    auth_source: str | None = None
