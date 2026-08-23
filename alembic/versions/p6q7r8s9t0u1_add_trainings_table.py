"""add trainings table

Revision ID: p6q7r8s9t0u1
Revises: o5p6q7r8s9t0
Create Date: 2026-08-23

"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "p6q7r8s9t0u1"
down_revision: Union[str, None] = "o5p6q7r8s9t0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table(
        "trainings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("enterprise_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("enterprises.id"), nullable=False, index=True),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("enterprise_locations.id"), nullable=True, index=True),
        sa.Column("title", sa.String(255), nullable=False, index=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("category", sa.String(100), nullable=False, index=True),
        sa.Column("subcategory", sa.String(100), nullable=True),
        sa.Column("tags", postgresql.JSONB, nullable=True),
        sa.Column("instructor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requirements", sa.Text, nullable=True),
        sa.Column("primary_image", sa.Text, nullable=True),
        sa.Column("gallery_images", postgresql.JSONB, nullable=True),
        sa.Column("promotional_video", sa.Text, nullable=True),
        sa.Column("documents", postgresql.JSONB, nullable=True),
        sa.Column("delivery_mode", sa.String(20), nullable=True, index=True),
        sa.Column("course_type", sa.String(50), nullable=True),
        sa.Column("start_date", sa.DateTime, nullable=True),
        sa.Column("end_date", sa.DateTime, nullable=True),
        sa.Column("enrolment_start", sa.DateTime, nullable=True),
        sa.Column("enrolment_end", sa.DateTime, nullable=True),
        sa.Column("time_zone", sa.String(100), nullable=True),
        sa.Column("capacity", sa.String(50), nullable=True),
        sa.Column("price", sa.String(50), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("promo_price", sa.String(50), nullable=True),
        sa.Column("coupon_code", sa.String(50), nullable=True),
        sa.Column("sections", postgresql.JSONB, nullable=True),
        sa.Column("assessments", postgresql.JSONB, nullable=True),
        sa.Column("assignments", postgresql.JSONB, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft", index=True),
        sa.Column("is_deleted", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_trainings_tenant_enterprise", "trainings", ["tenant_id", "enterprise_id"])
    op.create_table(
        "training_enrolments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("training_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trainings.id"), nullable=False, index=True),
        sa.Column("participant_name", sa.String(255), nullable=False),
        sa.Column("participant_email", sa.String(255), nullable=False, index=True),
        sa.Column("group_enrol", sa.Boolean, nullable=True),
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "training_waitlist",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("training_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trainings.id"), nullable=False, index=True),
        sa.Column("participant_name", sa.String(255), nullable=False),
        sa.Column("participant_email", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )

def downgrade() -> None:
    op.drop_table("training_waitlist")
    op.drop_table("training_enrolments")
    op.drop_index("ix_trainings_tenant_enterprise", table_name="trainings")
    op.drop_table("trainings")
