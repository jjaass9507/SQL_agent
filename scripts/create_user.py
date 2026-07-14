"""CLI：建立使用者帳號（email + 密碼互動輸入 + role）。

尚無註冊頁前的 bootstrap 手段（例如建立第一個 admin 帳號）：

    python3 scripts/create_user.py --email admin@example.com --role admin

密碼以 `getpass` 互動輸入兩次確認，不會顯示於終端機或留在 shell history。
"""

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.repos import users as users_repo  # noqa: E402
from app.repos.db import get_session_factory  # noqa: E402
from app.services.auth_service import hash_password  # noqa: E402


async def _create(email: str, password: str, role: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        existing = await users_repo.get_user_by_email(db, email)
        if existing is not None:
            print(f"錯誤：{email} 已存在", file=sys.stderr)
            raise SystemExit(1)
        user = await users_repo.create_user(
            db, email=email, password_hash=hash_password(password), role=role
        )
        await db.commit()
        print(f"已建立使用者 {user.email}（role={user.role}, id={user.id}）")


def main() -> None:
    parser = argparse.ArgumentParser(description="建立 SQL Agent 使用者帳號")
    parser.add_argument("--email", required=True)
    parser.add_argument("--role", choices=["user", "admin"], default="user")
    args = parser.parse_args()

    password = getpass.getpass("密碼：")
    confirm = getpass.getpass("再次輸入密碼：")
    if password != confirm:
        print("錯誤：兩次輸入的密碼不一致", file=sys.stderr)
        raise SystemExit(1)
    if len(password) < 8:
        print("錯誤：密碼至少需要 8 個字元", file=sys.stderr)
        raise SystemExit(1)

    asyncio.run(_create(args.email, password, args.role))


if __name__ == "__main__":
    main()
