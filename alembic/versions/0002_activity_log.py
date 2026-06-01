"""Platform usage trail: activity_log table

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-01

"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'activity_log',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('session_id', sa.String(36), nullable=True),
        sa.Column('event', sa.String(50), nullable=False),
        sa.Column('detail', sa.JSON, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_activity_log_created_at', 'activity_log', ['created_at'])
    op.create_index('ix_activity_log_session_id', 'activity_log', ['session_id'])


def downgrade() -> None:
    op.drop_table('activity_log')
