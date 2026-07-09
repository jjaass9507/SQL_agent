"""Add change_requests table (Phase 4: HITL DDL approval)

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-09

"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'change_requests',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('db_name', sa.String(255), nullable=True),
        sa.Column('ddl', sa.Text, nullable=False),
        sa.Column('reason', sa.Text, nullable=False, server_default=''),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('dry_run_ok', sa.Boolean, nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error', sa.Text, nullable=True),
    )
    op.create_index('ix_change_requests_status', 'change_requests', ['status'])
    op.create_index('ix_change_requests_created_at', 'change_requests', ['created_at'])


def downgrade() -> None:
    op.drop_table('change_requests')
