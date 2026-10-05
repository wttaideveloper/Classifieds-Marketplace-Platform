"""Idempotency log for system-generated workflow notifications."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b9d4f2a6c8e1"
down_revision = "a7c3e91d4b52"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "notification_event_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dedupe_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("dedupe_key", name="uq_notification_event_log_dedupe_key"),
    )


def downgrade():
    op.drop_table("notification_event_log")
