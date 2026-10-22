"""Preserve assessment configuration and grading as submitted.

Revision ID: a4b8c2d6e901
Revises: c8f4a2d6b9e1
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a4b8c2d6e901"
down_revision = "c8f4a2d6b9e1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("training_assessment_submissions", sa.Column("review_snapshot", postgresql.JSONB(), nullable=True))
    op.create_index("ix_assessment_attempt_owner", "training_assessment_submissions", ["training_id", "assessment_id", "participant_email", "submitted_at", "id"])


def downgrade():
    op.drop_index("ix_assessment_attempt_owner", table_name="training_assessment_submissions")
    op.drop_column("training_assessment_submissions", "review_snapshot")
