"""add onboarding form template tables

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-30 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "onboarding_forms",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_onboarding_forms_name", "onboarding_forms", ["name"])
    op.create_index("ix_onboarding_forms_entity_type", "onboarding_forms", ["entity_type"])
    op.create_index("ix_onboarding_forms_status", "onboarding_forms", ["status"])
    op.create_index(
        "ix_onboarding_forms_entity_status",
        "onboarding_forms",
        ["entity_type", "status"],
    )

    op.create_table(
        "onboarding_form_sections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("form_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["form_id"], ["onboarding_forms.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("form_id", "order", name="uq_onboarding_form_sections_form_order"),
    )
    op.create_index(
        "ix_onboarding_form_sections_form_id",
        "onboarding_form_sections",
        ["form_id"],
    )

    op.create_table(
        "onboarding_form_fields",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("field_key", sa.String(length=100), nullable=False),
        sa.Column("field_type", sa.String(length=50), nullable=False),
        sa.Column("placeholder", sa.String(length=255), nullable=True),
        sa.Column("help_text", sa.Text(), nullable=True),
        sa.Column("required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("locked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("visible", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["section_id"],
            ["onboarding_form_sections.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("section_id", "order", name="uq_onboarding_form_fields_section_order"),
        sa.UniqueConstraint("section_id", "field_key", name="uq_onboarding_form_fields_section_key"),
    )
    op.create_index(
        "ix_onboarding_form_fields_section_id",
        "onboarding_form_fields",
        ["section_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_onboarding_form_fields_section_id", table_name="onboarding_form_fields")
    op.drop_table("onboarding_form_fields")
    op.drop_index("ix_onboarding_form_sections_form_id", table_name="onboarding_form_sections")
    op.drop_table("onboarding_form_sections")
    op.drop_index("ix_onboarding_forms_entity_status", table_name="onboarding_forms")
    op.drop_index("ix_onboarding_forms_status", table_name="onboarding_forms")
    op.drop_index("ix_onboarding_forms_entity_type", table_name="onboarding_forms")
    op.drop_index("ix_onboarding_forms_name", table_name="onboarding_forms")
    op.drop_table("onboarding_forms")
