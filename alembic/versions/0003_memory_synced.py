"""Add sessions.memory_synced flag

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-02

"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'sessions',
        sa.Column('memory_synced', sa.Boolean(), nullable=False,
                  server_default=sa.text('false')),
    )


def downgrade() -> None:
    op.drop_column('sessions', 'memory_synced')
