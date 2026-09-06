"""add training last_admin_notes for approval request-changes flow

Revision ID: e2f3a4b5c6d7
Revises: e1f2a3b4c5d6
Create Date: 2026-09-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("trainings", sa.Column("last_admin_notes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("trainings", "last_admin_notes")
