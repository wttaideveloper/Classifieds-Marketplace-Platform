"""add instructor_photo, instructor_credentials, prerequisites, subtitle,
release_rule, scheduled_publication, randomise, is_mandatory, faqs, badges
to trainings (field-registry backfill)

Revision ID: q0r1s2t3u4v5
Revises: p9q0r1s2t3u4
Create Date: 2026-09-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "q0r1s2t3u4v5"
down_revision: Union[str, Sequence[str], None] = "p9q0r1s2t3u4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("trainings", sa.Column("instructor_photo", sa.Text(), nullable=True))
    op.add_column("trainings", sa.Column("instructor_credentials", sa.Text(), nullable=True))
    op.add_column("trainings", sa.Column("prerequisites", JSONB, nullable=True, server_default="[]"))
    op.add_column("trainings", sa.Column("subtitle", sa.String(255), nullable=True))
    op.add_column("trainings", sa.Column("release_rule", JSONB, nullable=True))
    op.add_column("trainings", sa.Column("scheduled_publication", sa.DateTime(), nullable=True))
    op.add_column("trainings", sa.Column("randomise", sa.Boolean(), nullable=True, server_default=sa.false()))
    op.add_column("trainings", sa.Column("is_mandatory", sa.Boolean(), nullable=True, server_default=sa.false()))
    op.add_column("trainings", sa.Column("faqs", JSONB, nullable=True, server_default="[]"))
    op.add_column("trainings", sa.Column("badges", JSONB, nullable=True, server_default="[]"))


def downgrade() -> None:
    op.drop_column("trainings", "badges")
    op.drop_column("trainings", "faqs")
    op.drop_column("trainings", "is_mandatory")
    op.drop_column("trainings", "randomise")
    op.drop_column("trainings", "scheduled_publication")
    op.drop_column("trainings", "release_rule")
    op.drop_column("trainings", "subtitle")
    op.drop_column("trainings", "prerequisites")
    op.drop_column("trainings", "instructor_credentials")
    op.drop_column("trainings", "instructor_photo")
