"""Service bookings: booking settings on services, the bookings table and its history.

Revision ID: p5a8c1e4f7b2
Revises: n3e6a9c2d7f4

services: delivery_modes, booking_enabled, requires_approval, time_zone, cancellation_rules, venue_instructions, meeting_* (all nullable or defaulted:
existing services keep working and are bookable with the defaults described in docs/service-bookings.md).
service_bookings / service_booking_events: new tables. Payment is a status column only.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "p5a8c1e4f7b2"
down_revision = "n3e6a9c2d7f4"
branch_labels = None
depends_on = None

BOOKING_INDEXES = (
    ("ix_service_bookings_tenant_id", ["tenant_id"]), ("ix_service_bookings_enterprise_id", ["enterprise_id"]), ("ix_service_bookings_service_id", ["service_id"]),
    ("ix_service_bookings_customer_user_id", ["customer_user_id"]), ("ix_service_bookings_provider_user_id", ["provider_user_id"]),
    ("ix_service_bookings_starts_at", ["starts_at"]), ("ix_service_bookings_status", ["status"]), ("ix_service_bookings_payment_status", ["payment_status"]),
    ("ix_service_bookings_service_start", ["service_id", "starts_at"]), ("ix_service_bookings_provider_start", ["provider_user_id", "starts_at"]),
    ("ix_service_bookings_enterprise_status", ["enterprise_id", "status"]),
)
SERVICE_COLUMNS = ("meeting_instructions", "meeting_passcode", "meeting_provider", "meeting_url", "venue_instructions", "cancellation_rules", "time_zone",
                   "requires_approval", "booking_enabled", "delivery_modes")


def upgrade():
    op.add_column("services", sa.Column("delivery_modes", postgresql.JSONB(), nullable=True))
    op.add_column("services", sa.Column("booking_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("services", sa.Column("requires_approval", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("services", sa.Column("time_zone", sa.String(64), nullable=True, server_default="Asia/Kolkata"))
    op.add_column("services", sa.Column("cancellation_rules", postgresql.JSONB(), nullable=True))
    op.add_column("services", sa.Column("venue_instructions", sa.Text(), nullable=True))
    op.add_column("services", sa.Column("meeting_url", sa.Text(), nullable=True))
    op.add_column("services", sa.Column("meeting_provider", sa.String(30), nullable=True))
    op.add_column("services", sa.Column("meeting_passcode", sa.String(100), nullable=True))
    op.add_column("services", sa.Column("meeting_instructions", sa.Text(), nullable=True))

    op.create_table(
        "service_bookings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("enterprise_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("enterprises.id"), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("services.id"), nullable=False),
        sa.Column("customer_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("customer_name", sa.String(255)),
        sa.Column("customer_email", sa.String(255)),
        sa.Column("customer_phone", sa.String(40)),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_role", sa.String(20)),
        sa.Column("provider_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_name", sa.String(255)),
        sa.Column("delivery_mode", sa.String(12), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("time_zone", sa.String(64), nullable=False),
        sa.Column("participants", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("status_before_request", sa.String(24)),
        sa.Column("payment_status", sa.String(20), nullable=False, server_default="not_required"),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3)),
        sa.Column("paid_at", sa.DateTime()),
        sa.Column("hold_expires_at", sa.DateTime()),
        sa.Column("notes", sa.Text()),
        sa.Column("cancel_reason", sa.Text()),
        sa.Column("cancelled_at", sa.DateTime()),
        sa.Column("cancelled_by", postgresql.UUID(as_uuid=True)),
        sa.Column("cancelled_by_role", sa.String(20)),
        sa.Column("cancellation_requested_at", sa.DateTime()),
        sa.Column("decision_reason", sa.Text()),
        sa.Column("refund_percent", sa.Integer()),
        sa.Column("refund_amount", sa.Float()),
        sa.Column("checked_in_at", sa.DateTime()),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("idempotency_key", sa.String(100)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("created_by", "idempotency_key", name="uq_service_bookings_idempotency"),
    )
    for name, cols in BOOKING_INDEXES:
        op.create_index(name, "service_bookings", cols)

    op.create_table(
        "service_booking_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("service_bookings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("from_status", sa.String(24)),
        sa.Column("to_status", sa.String(24)),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True)),
        sa.Column("actor_role", sa.String(20)),
        sa.Column("actor_name", sa.String(255)),
        sa.Column("reason", sa.Text()),
        sa.Column("detail", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_service_booking_events_booking_id", "service_booking_events", ["booking_id"])


def downgrade():
    op.drop_index("ix_service_booking_events_booking_id", table_name="service_booking_events")
    op.drop_table("service_booking_events")
    for name, _cols in reversed(BOOKING_INDEXES):
        op.drop_index(name, table_name="service_bookings")
    op.drop_table("service_bookings")
    for col in SERVICE_COLUMNS:
        op.drop_column("services", col)
