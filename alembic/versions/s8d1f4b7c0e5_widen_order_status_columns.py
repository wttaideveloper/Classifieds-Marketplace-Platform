"""Widen the order status columns: "cancellation_requested" is 22 characters and they were VARCHAR(20).

Revision ID: s8d1f4b7c0e5
Revises: q6f182cbd701

PostgreSQL refused "UPDATE orders SET status = 'cancellation_requested'" with StringDataRightTruncation, so a customer's cancellation request on a paid order
answered 500. SQLite (the tests) does not enforce the length, which is why nothing caught it.

orders.status, orders.status_before_request, order_status_events.from_status and .to_status become VARCHAR(32). Widening only: nothing is rewritten or lost.
"""
import sqlalchemy as sa
from alembic import op

revision = "s8d1f4b7c0e5"
down_revision = "q6f182cbd701"
branch_labels = None
depends_on = None

COLUMNS = (("orders", "status", False), ("orders", "status_before_request", True), ("order_status_events", "from_status", True), ("order_status_events", "to_status", True))


def upgrade():
    for table, column, nullable in COLUMNS:
        op.alter_column(table, column, type_=sa.String(32), existing_type=sa.String(20), existing_nullable=nullable)


def downgrade():
    # Deliberately empty: narrowing back to 20 characters would fail as soon as one order is "cancellation_requested", and the wider column does no harm.
    pass
