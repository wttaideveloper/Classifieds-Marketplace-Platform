"""add conversation archived_at

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-07-09

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "i9j0k1l2m3n4"
down_revision: Union[str, None] = "h8i9j0k1l2m3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("archived_at", sa.DateTime(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE conversations SET archived_at = updated_at WHERE status = 'archived'"
        )
    )


def downgrade() -> None:
    op.drop_column("conversations", "archived_at")
