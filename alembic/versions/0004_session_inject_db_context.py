"""sessions 新增 inject_db_context，並回收 app_settings 裡的 sticky 旗標

原本 interview 的「既有結構注入」sticky 旗標存在 `app_settings`，key 是
`session_context_sticky:<session_id>`——那是「不得改動 models.py」凍結期的權宜
之計（凍結已於 2026-08 解除，見 HANDOFF.md §6.1）。那個形狀會隨 session 數量
無限長大，且不會跟著 session 一起 CASCADE 刪除，因此收回 `sessions` 的欄位。

upgrade 把既有的 sticky 設定搬進新欄位再刪掉，downgrade 反向寫回，
所以來回一趟不會掉資料。

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-09 00:00:00.000000

"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEY_PREFIX = "session_context_sticky:"
_LIKE_PREFIX = f"{_KEY_PREFIX}%"


def _sticky_session_ids(conn) -> list[uuid.UUID]:
    """讀出所有 sticky=true 的 session id。

    `app_settings.value_json` 是通用 JSON 欄位：PostgreSQL 的驅動會把它解碼成
    True，SQLite 則回傳字串 "true"。兩種都要認得。
    """
    rows = conn.execute(
        sa.text("SELECT key, value_json FROM app_settings WHERE key LIKE :prefix").bindparams(
            prefix=_LIKE_PREFIX
        )
    ).fetchall()
    return [
        uuid.UUID(key[len(_KEY_PREFIX) :])
        for key, value in rows
        if value is True or (isinstance(value, str) and value.strip().lower() == "true")
    ]


def upgrade() -> None:
    """Upgrade schema."""
    # SQLite 不支援對既有表直接加 NOT NULL 欄位，batch mode 會重建整張表
    # （PostgreSQL 上等同一般 ALTER TABLE），兩邊皆相容。
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "inject_db_context",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    conn = op.get_bind()
    update = sa.text("UPDATE sessions SET inject_db_context = :flag WHERE id = :sid").bindparams(
        sa.bindparam("flag", type_=sa.Boolean()),
        sa.bindparam("sid", type_=sa.Uuid()),
    )
    for session_id in _sticky_session_ids(conn):
        conn.execute(update, {"flag": True, "sid": session_id})

    conn.execute(
        sa.text("DELETE FROM app_settings WHERE key LIKE :prefix").bindparams(prefix=_LIKE_PREFIX)
    )


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    # 用 .columns() 標註型別，id 才會還原成 uuid.UUID——SQLite 存的是無連字號的
    # 32 字元 hex，直接取字串會拼出跟原本不一樣的 key。
    rows = conn.execute(
        sa.text("SELECT id FROM sessions WHERE inject_db_context = :flag")
        .bindparams(sa.bindparam("flag", True, type_=sa.Boolean()))
        .columns(sa.column("id", sa.Uuid()))
    ).fetchall()

    # 寫回前先清掉可能殘留的同 key 記錄，避免主鍵衝突。
    conn.execute(
        sa.text("DELETE FROM app_settings WHERE key LIKE :prefix").bindparams(prefix=_LIKE_PREFIX)
    )
    insert = sa.text("INSERT INTO app_settings (key, value_json) VALUES (:key, :value)").bindparams(
        sa.bindparam("value", type_=sa.JSON())
    )
    for (session_id,) in rows:
        conn.execute(insert, {"key": f"{_KEY_PREFIX}{session_id}", "value": True})

    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_column("inject_db_context")
