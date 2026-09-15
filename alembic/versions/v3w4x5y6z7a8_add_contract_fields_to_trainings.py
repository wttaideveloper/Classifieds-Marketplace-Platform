"""add duration_hours, meeting_id, meeting_passcode, moderation_status,
rejection_reason, published_at, approved_at, archived_at, suspended_at,
cancelled_at to trainings (Training/Course response-contract fields)

Revision ID: v3w4x5y6z7a8
Revises: u2v3w4x5y6z7
Create Date: 2026-09-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v3w4x5y6z7a8"
down_revision: Union[str, Sequence[str], None] = "u2v3w4x5y6z7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("trainings", sa.Column("duration_hours", sa.String(20), nullable=True))
    op.add_column("trainings", sa.Column("meeting_id", sa.String(100), nullable=True))
    op.add_column("trainings", sa.Column("meeting_passcode", sa.String(50), nullable=True))
    op.add_column("trainings", sa.Column("moderation_status", sa.String(20), nullable=True, server_default="draft"))
    op.add_column("trainings", sa.Column("rejection_reason", sa.Text(), nullable=True))
    op.add_column("trainings", sa.Column("published_at", sa.DateTime(), nullable=True))
    op.add_column("trainings", sa.Column("approved_at", sa.DateTime(), nullable=True))
    op.add_column("trainings", sa.Column("archived_at", sa.DateTime(), nullable=True))
    op.add_column("trainings", sa.Column("suspended_at", sa.DateTime(), nullable=True))
    op.add_column("trainings", sa.Column("cancelled_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("trainings", "cancelled_at")
    op.drop_column("trainings", "suspended_at")
    op.drop_column("trainings", "archived_at")
    op.drop_column("trainings", "approved_at")
    op.drop_column("trainings", "published_at")
    op.drop_column("trainings", "rejection_reason")
    op.drop_column("trainings", "moderation_status")
    op.drop_column("trainings", "meeting_passcode")
    op.drop_column("trainings", "meeting_id")
    op.drop_column("trainings", "duration_hours")
