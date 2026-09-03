"""add training announcements moderation and live attendance"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "trainings",
        sa.Column("announcements", JSONB, nullable=True, server_default="[]"),
    )
    op.add_column(
        "trainings",
        sa.Column("moderation_history", JSONB, nullable=True, server_default="[]"),
    )
    op.add_column(
        "training_live_sessions",
        sa.Column("attendance", JSONB, nullable=True, server_default="[]"),
    )
    op.add_column(
        "program_enrolments",
        sa.Column("new_end_date", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "program_enrolments",
        sa.Column("withdrawal_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "program_enrolments",
        sa.Column("participant_goals", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("program_enrolments", "participant_goals")
    op.drop_column("program_enrolments", "withdrawal_reason")
    op.drop_column("program_enrolments", "new_end_date")
    op.drop_column("training_live_sessions", "attendance")
    op.drop_column("trainings", "moderation_history")
    op.drop_column("trainings", "announcements")
