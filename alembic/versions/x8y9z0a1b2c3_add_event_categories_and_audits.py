"""add event_categories and event_audits

Revision ID: x8y9z0a1b2c3
Revises: w7x8y9z0a1b2
Create Date: 2026-08-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = 'x8y9z0a1b2c3'
down_revision = 'w7x8y9z0a1b2'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('event_categories',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False, unique=True, index=True),
        sa.Column('parent_id', UUID(as_uuid=True), sa.ForeignKey('event_categories.id'), nullable=True, index=True),
        sa.Column('description', sa.Text),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('now()')),
    )
    op.create_table('event_audits',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('event_id', UUID(as_uuid=True), sa.ForeignKey('events.id'), nullable=False, index=True),
        sa.Column('changed_by', sa.String(255)),
        sa.Column('action', sa.String(50), nullable=False),
        sa.Column('before', JSONB),
        sa.Column('after', JSONB),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('now()')),
    )

def downgrade():
    op.drop_table('event_audits')
    op.drop_table('event_categories')
