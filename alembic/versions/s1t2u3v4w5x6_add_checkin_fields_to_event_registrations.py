"""add checkin fields to event_registrations

Revision ID: s1t2u3v4w5x6
Revises: r1s2t3u4v5w6
Create Date: 2026-08-26

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers
revision = 's1t2u3v4w5x6'
down_revision = 'r1s2t3u4v5w6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('event_registrations', sa.Column('checked_in_at', sa.DateTime(), nullable=True))
    op.add_column('event_registrations', sa.Column('checked_in_by', UUID(as_uuid=True), nullable=True))
    op.add_column('event_registrations', sa.Column('session_id', sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column('event_registrations', 'session_id')
    op.drop_column('event_registrations', 'checked_in_by')
    op.drop_column('event_registrations', 'checked_in_at')
