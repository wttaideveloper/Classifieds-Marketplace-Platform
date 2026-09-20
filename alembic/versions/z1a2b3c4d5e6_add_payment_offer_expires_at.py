"""add payment_offer_expires_at to event_waitlist

Revision ID: z1a2b3c4d5e6
Revises: z0a1b2c3d4e5f6
Create Date: 2026-09-20 13:24:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'z1a2b3c4d5e6'
down_revision = 'z0a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('event_waitlist', sa.Column('payment_offer_expires_at', sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column('event_waitlist', 'payment_offer_expires_at')
