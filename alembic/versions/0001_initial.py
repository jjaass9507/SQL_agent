"""Initial schema: sessions and messages tables

Revision ID: 0001
Revises:
Create Date: 2026-05-29

"""
from alembic import op
import sqlalchemy as sa

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'sessions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('title', sa.Text, nullable=False),
        sa.Column('mode', sa.String(20), nullable=False, server_default='design'),
        sa.Column('phase', sa.String(30), nullable=False),
        sa.Column('key_points', sa.JSON, nullable=False, server_default='[]'),
        sa.Column('tables', sa.JSON, nullable=True),
        sa.Column('table_versions', sa.JSON, nullable=False, server_default='[]'),
        sa.Column('outputs', sa.JSON, nullable=False, server_default='{}'),
        sa.Column('generation_status', sa.JSON, nullable=False, server_default='{}'),
        sa.Column('generation_errors', sa.JSON, nullable=False, server_default='{}'),
        sa.Column('context_tables', sa.JSON, nullable=False, server_default='[]'),
        sa.Column('context_text', sa.Text, nullable=False, server_default=''),
        sa.Column('last_db_import', sa.JSON, nullable=True),
        sa.Column('db_url', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_sessions_created_at', 'sessions', ['created_at'])

    op.create_table(
        'messages',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('session_id', sa.String(36),
                  sa.ForeignKey('sessions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.String(10), nullable=False),
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_messages_session_id', 'messages', ['session_id'])


def downgrade() -> None:
    op.drop_table('messages')
    op.drop_table('sessions')
