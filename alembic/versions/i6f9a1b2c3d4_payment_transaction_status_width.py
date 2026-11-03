"""Widen pay_payment_transactions.status so gateway status strings fit.

Revision ID: i6f9a1b2c3d4
Revises: h5d0f3c8e2b4
"""
from alembic import op
import sqlalchemy as sa

revision = "i6f9a1b2c3d4"
down_revision = "h5d0f3c8e2b4"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "pay_payment_transactions",
        "status",
        existing_type=sa.String(length=20),
        type_=sa.String(length=32),
        existing_nullable=True,
    )


def downgrade():
    op.alter_column(
        "pay_payment_transactions",
        "status",
        existing_type=sa.String(length=32),
        type_=sa.String(length=20),
        existing_nullable=True,
    )
