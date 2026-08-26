"""add checked_out_at to event_registrations

Revision ID: t2u3v4w5x6y7
Revises: s1t2u3v4w5x6
Create Date: 2026-08-26

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 't2u3v4w5x6y7'
down_revision = 's1t2u3v4w5x6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('event_registrations', sa.Column('checked_out_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('event_registrations', 'checked_out_at')
