"""AD 登入支援：users 新增 auth_source / display_name，password_hash 改 nullable

AD（Active Directory）登入 JIT 供裝的使用者不落地本地密碼，
password_hash 因此改為可為 NULL；auth_source 標記帳號來源
（'local' 或 'ad'），見 app/services/ad_auth.py。

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # SQLite 不支援直接 ALTER COLUMN／新增 CHECK 約束，batch mode 會重建整張表
    # （PostgreSQL 上則等同一般 ALTER TABLE），兩邊皆相容。
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column(
                "auth_source", sa.String(length=20), nullable=False, server_default="local"
            )
        )
        batch_op.add_column(sa.Column("display_name", sa.String(length=200), nullable=True))
        batch_op.alter_column(
            "password_hash", existing_type=sa.String(length=255), nullable=True
        )
        batch_op.create_check_constraint(
            "ck_users_auth_source", "auth_source IN ('local','ad')"
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("ck_users_auth_source", type_="check")
        batch_op.alter_column(
            "password_hash", existing_type=sa.String(length=255), nullable=False
        )
        batch_op.drop_column("display_name")
        batch_op.drop_column("auth_source")
