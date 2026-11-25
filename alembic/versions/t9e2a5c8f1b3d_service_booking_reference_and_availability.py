"""Service bookings: a booking reference; services: per-service notice windows and blocked dates / times.

Revision ID: t9e2a5c8f1b3d
Revises: r7c0e3a6b9d4

service_bookings.reference   "SB-" + the first 10 hex digits of the id, upper case. Existing rows are filled from their id (no row changes otherwise), then a unique index.
services.min_notice_minutes, services.max_advance_days, services.blocked_periods   all nullable: null keeps today's behaviour (the platform defaults, no blocked dates).
service_bookings.refund_status   independent of payment_status. Rows with a recorded refund (payment_status refunded / partially_refunded) get "pending":
                                 no gateway refund was ever made for them. payment_status itself is left exactly as it is.
Additive only; nothing existing is changed or removed.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "t9e2a5c8f1b3d"
down_revision = "r7c0e3a6b9d4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("service_bookings", sa.Column("reference", sa.String(20), nullable=True))
    op.execute("UPDATE service_bookings SET reference = 'SB-' || upper(substr(replace(id::text, '-', ''), 1, 10)) WHERE reference IS NULL")
    op.create_index("ix_service_bookings_reference", "service_bookings", ["reference"], unique=True)
    op.add_column("service_bookings", sa.Column("refund_status", sa.String(20), nullable=True))
    op.execute("UPDATE service_bookings SET refund_status = 'pending' WHERE payment_status IN ('refunded', 'partially_refunded') AND refund_status IS NULL")
    op.create_index("ix_service_bookings_refund_status", "service_bookings", ["refund_status"])
    op.add_column("services", sa.Column("min_notice_minutes", sa.Integer(), nullable=True))
    op.add_column("services", sa.Column("max_advance_days", sa.Integer(), nullable=True))
    op.add_column("services", sa.Column("blocked_periods", postgresql.JSONB(), nullable=True))


def downgrade():
    op.drop_column("services", "blocked_periods")
    op.drop_column("services", "max_advance_days")
    op.drop_column("services", "min_notice_minutes")
    op.drop_index("ix_service_bookings_refund_status", table_name="service_bookings")
    op.drop_column("service_bookings", "refund_status")
    op.drop_index("ix_service_bookings_reference", table_name="service_bookings")
    op.drop_column("service_bookings", "reference")
