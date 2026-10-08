"""Event reviews: remember who wrote a review, and when it last changed.

user_id lets the author be told about a moderation decision and lets a person find their own review.
updated_at changes whenever a review is edited or moderated.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f2b4d6a8c0e1"
down_revision = "e9a1c3b5d7f2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("event_feedback", sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_event_feedback_user_id", "event_feedback", ["user_id"])
    op.add_column("event_feedback", sa.Column("updated_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE event_feedback SET updated_at = created_at")


def downgrade():
    op.drop_column("event_feedback", "updated_at")
    op.drop_index("ix_event_feedback_user_id", table_name="event_feedback")
    op.drop_column("event_feedback", "user_id")
