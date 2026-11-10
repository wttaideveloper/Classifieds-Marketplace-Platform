"""Event waitlist: the waitlisted person's platform user id (so waitlist push notifications reach their phone).

Revision ID: o4f7b0d3e8a5
Revises: n3e6a9c2d7f4

event_waitlist.user_id  nullable, no foreign key. Saved when the person joins the waitlist themselves (verified identity); older rows stay NULL
                        and are reached by email like before.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "o4f7b0d3e8a5"
down_revision = "n3e6a9c2d7f4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("event_waitlist", sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_event_waitlist_user_id", "event_waitlist", ["user_id"], if_not_exists=True)


def downgrade():
    op.drop_index("ix_event_waitlist_user_id", table_name="event_waitlist", if_exists=True)
    op.drop_column("event_waitlist", "user_id")
