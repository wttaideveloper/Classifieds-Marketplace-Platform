"""add programs table

Revision ID: q7r8s9t0u1v2
Revises: p6q7r8s9t0u1
Create Date: 2026-08-23

"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "q7r8s9t0u1v2"
down_revision: Union[str, None] = "p6q7r8s9t0u1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table(
        "programs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("enterprise_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("enterprises.id"), nullable=False, index=True),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("enterprise_locations.id"), nullable=True, index=True),
        sa.Column("title", sa.String(255), nullable=False, index=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("category", sa.String(100), nullable=False, index=True),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("duration_weeks", sa.String(50), nullable=True),
        sa.Column("eligibility", postgresql.JSONB, nullable=True),
        sa.Column("start_date", sa.DateTime, nullable=True),
        sa.Column("end_date", sa.DateTime, nullable=True),
        sa.Column("enrolment_start", sa.DateTime, nullable=True),
        sa.Column("enrolment_end", sa.DateTime, nullable=True),
        sa.Column("enrol_type", sa.String(20), nullable=True),
        sa.Column("delivery_mode", sa.String(20), nullable=True, index=True),
        sa.Column("price", sa.String(50), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("capacity", sa.String(50), nullable=True),
        sa.Column("phases", postgresql.JSONB, nullable=True),
        sa.Column("goals", postgresql.JSONB, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft", index=True),
        sa.Column("is_deleted", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_programs_tenant_enterprise", "programs", ["tenant_id", "enterprise_id"])
    op.create_table(
        "program_enrolments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("program_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("programs.id"), nullable=False, index=True),
        sa.Column("participant_name", sa.String(255), nullable=False),
        sa.Column("participant_email", sa.String(255), nullable=False, index=True),
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "program_checkins",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("program_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("programs.id"), nullable=False, index=True),
        sa.Column("participant_email", sa.String(255), nullable=False),
        sa.Column("phase_id", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )

def downgrade() -> None:
    op.drop_table("program_checkins")
    op.drop_table("program_enrolments")
    op.drop_index("ix_programs_tenant_enterprise", table_name="programs")
    op.drop_table("programs")
