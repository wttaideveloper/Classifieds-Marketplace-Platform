"""add service detail and product dimension fields

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-06-19 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("services", sa.Column("banner_image", sa.Text(), nullable=True))
    op.add_column("services", sa.Column("service_type", sa.String(length=100), nullable=True))

    op.add_column("products", sa.Column("length", sa.Float(), nullable=True))
    op.add_column("products", sa.Column("width", sa.Float(), nullable=True))
    op.add_column("products", sa.Column("thick", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "thick")
    op.drop_column("products", "width")
    op.drop_column("products", "length")

    op.drop_column("services", "service_type")
    op.drop_column("services", "banner_image")
