"""Add TrainingLessonAttendance table — admin-marked per-lesson, per-enrolment attendance."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "8e1805a6e5e2"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "training_lesson_attendance",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("training_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trainings.id"), nullable=False),
        sa.Column("lesson_id", sa.String(255), nullable=False),
        sa.Column("enrolment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("training_enrolments.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("marked_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("marked_by_name", sa.String(255), nullable=True),
        sa.Column("marked_by_email", sa.String(255), nullable=True),
        sa.Column("marked_at", sa.DateTime(), nullable=True),
        sa.Column("history", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_training_lesson_attendance_training_id", "training_lesson_attendance", ["training_id"])
    op.create_index("ix_training_lesson_attendance_lookup", "training_lesson_attendance", ["training_id", "lesson_id"])
    op.create_index(
        "uq_training_lesson_attendance", "training_lesson_attendance",
        ["training_id", "lesson_id", "enrolment_id"], unique=True,
    )


def downgrade():
    op.drop_table("training_lesson_attendance")
