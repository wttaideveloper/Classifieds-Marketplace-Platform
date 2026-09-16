"""add event pricing_type

Revision ID: e2b1c2d3e4f7
Revises: a0b1c2d3e4f6
Create Date: 2026-09-16 13:55:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'e2b1c2d3e4f7'
down_revision = 'a0b1c2d3e4f6'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column('events', sa.Column('pricing_type', sa.String(length=20), server_default='free', nullable=False))
    op.create_index(op.f('ix_events_pricing_type'), 'events', ['pricing_type'], unique=False)

def downgrade() -> None:
    op.drop_index(op.f('ix_events_pricing_type'), table_name='events')
    op.drop_column('events', 'pricing_type')
