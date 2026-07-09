"""add attachment transcript fields

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-07-09

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "h8i9j0k1l2m3"
down_revision: Union[str, None] = "g7h8i9j0k1l2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chat_attachments", sa.Column("transcript", sa.Text(), nullable=True))
    op.add_column("chat_attachments", sa.Column("transcribed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_attachments", "transcribed_at")
    op.drop_column("chat_attachments", "transcript")
