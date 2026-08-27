"""add duration_type to events

Revision ID: v6w7x8y9z0a1
Revises: u6v7w8x9y0z1
Create Date: 2026-08-27
"""
from alembic import op
import sqlalchemy as sa

revision = 'v6w7x8y9z0a1'
down_revision = 'u6v7w8x9y0z1'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('events', sa.Column('duration_type', sa.String(20), nullable=True, server_default='custom'))
    op.create_index('ix_events_duration_type', 'events', ['duration_type'])

def downgrade():
    op.drop_index('ix_events_duration_type', table_name='events')
    op.drop_column('events', 'duration_type')
