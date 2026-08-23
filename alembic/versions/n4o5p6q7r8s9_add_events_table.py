"""add events table

Revision ID: n4o5p6q7r8s9
Revises: m3n4o5p6q7r8
Create Date: 2026-08-23

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "n4o5p6q7r8s9"
down_revision: Union[str, None] = "m3n4o5p6q7r8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("enterprise_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("enterprises.id"), nullable=False, index=True),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("enterprise_locations.id"), nullable=True, index=True),
        sa.Column("title", sa.String(255), nullable=False, index=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("category", sa.String(100), nullable=False, index=True),
        sa.Column("subcategory", sa.String(100), nullable=True),
        sa.Column("tags", postgresql.JSONB, nullable=True),
        sa.Column("organiser_name", sa.String(255), nullable=True),
        sa.Column("organiser_contact", sa.String(255), nullable=True),
        sa.Column("start_date", sa.DateTime, nullable=False),
        sa.Column("end_date", sa.DateTime, nullable=False),
        sa.Column("time_zone", sa.String(100), nullable=True),
        sa.Column("registration_cutoff", sa.DateTime, nullable=True),
        sa.Column("primary_image", sa.Text, nullable=True),
        sa.Column("gallery_images", postgresql.JSONB, nullable=True),
        sa.Column("videos", postgresql.JSONB, nullable=True),
        sa.Column("documents", postgresql.JSONB, nullable=True),
        sa.Column("delivery_mode", sa.String(20), nullable=True, index=True),
        sa.Column("venue", postgresql.JSONB, nullable=True),
        sa.Column("meeting_link", sa.Text, nullable=True),
        sa.Column("meeting_provider", sa.String(50), nullable=True),
        sa.Column("price", sa.String(50), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("ticket_types", postgresql.JSONB, nullable=True),
        sa.Column("capacity", sa.String(50), nullable=True),
        sa.Column("min_participants", sa.String(50), nullable=True),
        sa.Column("max_participants", sa.String(50), nullable=True),
        sa.Column("registration_open_at", sa.DateTime, nullable=True),
        sa.Column("registration_close_at", sa.DateTime, nullable=True),
        sa.Column("custom_fields", postgresql.JSONB, nullable=True),
        sa.Column("sessions", postgresql.JSONB, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft", index=True),
        sa.Column("is_deleted", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_events_tenant_enterprise", "events", ["tenant_id", "enterprise_id"])
    op.create_index("ix_events_enterprise_location", "events", ["enterprise_id", "location_id"])


def downgrade() -> None:
    op.drop_index("ix_events_enterprise_location", table_name="events")
    op.drop_index("ix_events_tenant_enterprise", table_name="events")
    op.drop_table("events")
