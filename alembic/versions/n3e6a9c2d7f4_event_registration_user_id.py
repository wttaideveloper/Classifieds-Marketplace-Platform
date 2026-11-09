"""Event registrations: the registrant's platform user id (for Event Group Chat), and the index its roster query uses.

Revision ID: n3e6a9c2d7f4
Revises: m2d5f8b3a6c1

event_registrations.user_id  nullable, no foreign key (users live in the identity service). Existing rows stay NULL: they are
                             linked when the registrant opens "my registrations", when the group roster is opened (by email,
                             from the enterprise's member list) or by scripts/backfill_event_registration_user_ids.py.
ix_event_registrations_event_status  the roster / eligibility queries filter one event's registrations by status.
Plain CREATE INDEX (locks writes while it builds); on a very large event_registrations create it CONCURRENTLY by hand first.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "n3e6a9c2d7f4"
down_revision = "m2d5f8b3a6c1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("event_registrations", sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_event_registrations_user_id", "event_registrations", ["user_id"], if_not_exists=True)
    op.create_index("ix_event_registrations_event_status", "event_registrations", ["event_id", "status"], if_not_exists=True)


def downgrade():
    op.drop_index("ix_event_registrations_event_status", table_name="event_registrations", if_exists=True)
    op.drop_index("ix_event_registrations_user_id", table_name="event_registrations", if_exists=True)
    op.drop_column("event_registrations", "user_id")
