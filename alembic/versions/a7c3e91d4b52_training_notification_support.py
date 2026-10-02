"""Training notifications: enrolment user_id + reminder claim log."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a7c3e91d4b52"
down_revision = "f631d3e45389"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("training_enrolments", sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_training_enrolments_user_id", "training_enrolments", ["user_id"])
    op.create_table(
        "training_notification_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("training_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trainings.id"), nullable=False),
        sa.Column("participant_email", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("ref_date", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_training_notification_log_training_id", "training_notification_log", ["training_id"])
    op.create_index(
        "uq_training_notification_log_claim",
        "training_notification_log",
        ["training_id", "participant_email", "kind", "ref_date"],
        unique=True,
    )


def downgrade():
    op.drop_table("training_notification_log")
    op.drop_index("ix_training_enrolments_user_id", table_name="training_enrolments")
    op.drop_column("training_enrolments", "user_id")
