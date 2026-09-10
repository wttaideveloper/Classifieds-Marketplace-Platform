"""add training detail fields (instructor, schedule, venue, meeting, docs)

Revision ID: k4l5m6n7o8p9
Revises: j3k4l5m6n7o8
Create Date: 2026-09-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "k4l5m6n7o8p9"
down_revision: Union[str, Sequence[str], None] = "j3k4l5m6n7o8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("trainings", sa.Column("instructor_name", sa.String(255), nullable=True))
    op.add_column("trainings", sa.Column("instructor_bio", sa.Text(), nullable=True))
    op.add_column("trainings", sa.Column("learning_objectives", JSONB, nullable=True, server_default="[]"))
    op.add_column("trainings", sa.Column("start_time", sa.String(20), nullable=True))
    op.add_column("trainings", sa.Column("end_time", sa.String(20), nullable=True))
    op.add_column("trainings", sa.Column("venue", sa.String(255), nullable=True))
    op.add_column("trainings", sa.Column("address", sa.Text(), nullable=True))
    op.add_column("trainings", sa.Column("meeting_link", sa.Text(), nullable=True))
    op.add_column("trainings", sa.Column("delivery_instructions", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("trainings", "delivery_instructions")
    op.drop_column("trainings", "meeting_link")
    op.drop_column("trainings", "address")
    op.drop_column("trainings", "venue")
    op.drop_column("trainings", "end_time")
    op.drop_column("trainings", "start_time")
    op.drop_column("trainings", "learning_objectives")
    op.drop_column("trainings", "instructor_bio")
    op.drop_column("trainings", "instructor_name")
