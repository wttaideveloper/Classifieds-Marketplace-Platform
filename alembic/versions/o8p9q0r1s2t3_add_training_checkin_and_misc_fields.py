"""add meeting_provider, access_information, recurring, schedule_exceptions,
instructor_notes, session_mode, check_in, pass_code, qr_payload to trainings;
checked_in_at to training_enrolments

Revision ID: o8p9q0r1s2t3
Revises: n7o8p9q0r1s2
Create Date: 2026-09-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "o8p9q0r1s2t3"
down_revision: Union[str, Sequence[str], None] = "n7o8p9q0r1s2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("trainings", sa.Column("meeting_provider", sa.String(20), nullable=True))
    op.add_column("trainings", sa.Column("access_information", sa.Text(), nullable=True))
    op.add_column("trainings", sa.Column("recurring", JSONB, nullable=True))
    op.add_column("trainings", sa.Column("schedule_exceptions", JSONB, nullable=True, server_default="[]"))
    op.add_column("trainings", sa.Column("instructor_notes", sa.Text(), nullable=True))
    op.add_column("trainings", sa.Column("session_mode", sa.String(20), nullable=True))
    op.add_column("trainings", sa.Column("check_in", sa.Boolean(), nullable=True, server_default=sa.false()))
    op.add_column("trainings", sa.Column("pass_code", sa.String(20), nullable=True))
    op.add_column("trainings", sa.Column("qr_payload", sa.Text(), nullable=True))
    op.add_column("training_enrolments", sa.Column("checked_in_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("training_enrolments", "checked_in_at")
    op.drop_column("trainings", "qr_payload")
    op.drop_column("trainings", "pass_code")
    op.drop_column("trainings", "check_in")
    op.drop_column("trainings", "session_mode")
    op.drop_column("trainings", "instructor_notes")
    op.drop_column("trainings", "schedule_exceptions")
    op.drop_column("trainings", "recurring")
    op.drop_column("trainings", "access_information")
    op.drop_column("trainings", "meeting_provider")
