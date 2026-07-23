"""add message edit fields

Revision ID: g7h8i9j0k1l2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-08

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "g7h8i9j0k1l2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("is_edited", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("messages", sa.Column("edited_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "edited_at")
    op.drop_column("messages", "is_edited")
