"""add event auxiliary tables

Revision ID: o5p6q7r8s9t0
Revises: n4o5p6q7r8s9
Create Date: 2026-08-23

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "o5p6q7r8s9t0"
down_revision: Union[str, None] = "n4o5p6q7r8s9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_registrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id"), nullable=False, index=True),
        sa.Column("participant_name", sa.String(255), nullable=False),
        sa.Column("participant_email", sa.String(255), nullable=False, index=True),
        sa.Column("custom_fields", postgresql.JSONB, nullable=True),
        sa.Column("ticket_type_id", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=True, index=True),
        sa.Column("qr_code", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "event_waitlist",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id"), nullable=False, index=True),
        sa.Column("participant_name", sa.String(255), nullable=False),
        sa.Column("participant_email", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "event_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("enterprise_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("enterprises.id"), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("template_data", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "event_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id"), nullable=False, index=True),
        sa.Column("participant_email", sa.String(255), nullable=True),
        sa.Column("form_id", sa.String(100), nullable=True),
        sa.Column("answers", postgresql.JSONB, nullable=True),
        sa.Column("rating", sa.String(10), nullable=True),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("is_review", sa.Boolean, nullable=True),
        sa.Column("moderation_status", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("event_feedback")
    op.drop_table("event_templates")
    op.drop_table("event_waitlist")
    op.drop_table("event_registrations")
