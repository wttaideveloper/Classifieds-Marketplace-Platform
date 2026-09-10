"""add refund_reason to orders

The Order model has declared refund_reason since the refund flow was added,
but no migration ever created the column on the `orders` table (it was only
ever added to the unrelated `event_orders` table). Every checkout INSERT
therefore fails in Postgres with "column refund_reason of relation orders
does not exist", surfacing as a 500 on POST /api/v1/cart/checkout.

Revision ID: j3k4l5m6n7o8
Revises: i2j3k4l5m6n7
Create Date: 2026-09-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "j3k4l5m6n7o8"
down_revision: Union[str, Sequence[str], None] = "i2j3k4l5m6n7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("refund_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "refund_reason")
