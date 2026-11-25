"""Orders: tracking, cancellation requests, stock bookkeeping and a status history.

Revision ID: q6b9d2f5a8c3
Revises: p5a8c1e4f7b2

orders: carrier, tracking_number, tracking_url, shipped_at, delivered_at, cancel_reason, cancelled_at / by / by_role, cancellation_requested_at, status_before_request,
        decision_reason, stock_reduced, stock_restocked (the last two default false: orders placed before this never took stock, so they never give it back).
order_status_events: new table (history / audit).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "q6b9d2f5a8c3"
down_revision = "p5a8c1e4f7b2"
branch_labels = None
depends_on = None

COLUMNS = (
    ("carrier", sa.String(80)), ("tracking_number", sa.String(120)), ("tracking_url", sa.Text()), ("shipped_at", sa.DateTime()), ("delivered_at", sa.DateTime()),
    ("cancel_reason", sa.Text()), ("cancelled_at", sa.DateTime()), ("cancelled_by", postgresql.UUID(as_uuid=True)), ("cancelled_by_role", sa.String(20)),
    ("cancellation_requested_at", sa.DateTime()), ("status_before_request", sa.String(20)), ("decision_reason", sa.Text()),
)


def upgrade():
    # one literal statement per column: tests/test_orders_schema_matches_migrations.py reads this file statically
    op.add_column("orders", sa.Column("carrier", sa.String(80), nullable=True))
    op.add_column("orders", sa.Column("tracking_number", sa.String(120), nullable=True))
    op.add_column("orders", sa.Column("tracking_url", sa.Text(), nullable=True))
    op.add_column("orders", sa.Column("shipped_at", sa.DateTime(), nullable=True))
    op.add_column("orders", sa.Column("delivered_at", sa.DateTime(), nullable=True))
    op.add_column("orders", sa.Column("cancel_reason", sa.Text(), nullable=True))
    op.add_column("orders", sa.Column("cancelled_at", sa.DateTime(), nullable=True))
    op.add_column("orders", sa.Column("cancelled_by", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("orders", sa.Column("cancelled_by_role", sa.String(20), nullable=True))
    op.add_column("orders", sa.Column("cancellation_requested_at", sa.DateTime(), nullable=True))
    op.add_column("orders", sa.Column("status_before_request", sa.String(20), nullable=True))
    op.add_column("orders", sa.Column("decision_reason", sa.Text(), nullable=True))
    op.add_column("orders", sa.Column("stock_reduced", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("orders", sa.Column("stock_restocked", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_orders_tenant_status", "orders", ["tenant_id", "status"])
    op.create_table(
        "order_status_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("from_status", sa.String(20)),
        sa.Column("to_status", sa.String(20)),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True)),
        sa.Column("actor_role", sa.String(20)),
        sa.Column("reason", sa.Text()),
        sa.Column("detail", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_order_status_events_order_id", "order_status_events", ["order_id"])


def downgrade():
    op.drop_index("ix_order_status_events_order_id", table_name="order_status_events")
    op.drop_table("order_status_events")
    op.drop_index("ix_orders_tenant_status", table_name="orders")
    for name in ("stock_restocked", "stock_reduced"):
        op.drop_column("orders", name)
    for name, _type in reversed(COLUMNS):
        op.drop_column("orders", name)
