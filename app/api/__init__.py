"""API 層：HTTP 進出與驗證，不含業務邏輯。

`all_routers()` 自動探索 `app/api/routers/` 下每個模組的 module-level
`router`（APIRouter 實例）——新增一組 API ＝ 在 routers/ 加一個新檔，
不需要修改任何共用檔（平行開發零合併衝突的關鍵）。
"""

import importlib
import pkgutil

from fastapi import APIRouter

from app.api import routers as _routers_pkg


def all_routers() -> list[APIRouter]:
    """回傳 app/api/routers/ 下所有模組的 module-level `router`。"""
    found: list[APIRouter] = []
    for module_info in pkgutil.iter_modules(_routers_pkg.__path__):
        module = importlib.import_module(f"{_routers_pkg.__name__}.{module_info.name}")
        router = getattr(module, "router", None)
        if isinstance(router, APIRouter):
            found.append(router)
    return found
