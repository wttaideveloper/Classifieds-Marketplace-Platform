"""add catalog display fields for products

Revision ID: e3f4a5b6c7d8
Revises: e2f3a4b5c6d7
Create Date: 2026-09-08 12:15:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("listing_type", sa.String(length=50), nullable=False, server_default="one_time"),
    )
    op.add_column("products", sa.Column("delivery_interval", sa.String(length=100), nullable=True))
    op.add_column("products", sa.Column("delivery_fee", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "delivery_fee")
    op.drop_column("products", "delivery_interval")
    op.drop_column("products", "listing_type")
