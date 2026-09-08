"""add training form configuration domain and training form references

Revision ID: g5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-09-08
"""
from typing import Sequence, Union
import json
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "g5b6c7d8e9f0"
down_revision: Union[str, Sequence[str], None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_CONFIGURATION_ID = "00000000-0000-4000-8000-000000000011"
DEFAULT_VERSION_ID = "00000000-0000-4000-8000-000000000012"


def _seed_sections() -> list:
    """1 section, 3 fields: title / category / price (mirrors training_form_registry.build_seed_sections)."""

    def f(core_key, label, renderer, pos, required=False):
        return {
            "id": f"field_{core_key}",
            "source": "core",
            "core_key": core_key,
            "stable_key": core_key,
            "label": label,
            "renderer": renderer,
            "value_type": "string",
            "required": required,
            "is_enabled": True,
            "position": pos,
            "placeholder": None,
            "help_text": None,
            "options": [],
            "validation": {},
            "composite_config": None,
        }

    return [
        {
            "id": "section_basic",
            "stable_key": "basic",
            "label": "Training Details",
            "description": "Super Admin default Training form (Global)",
            "position": 1,
            "is_enabled": True,
            "fields": [
                f("title", "Title", "text", 1, True),
                f("category", "Category", "text", 2, True),
                f("price", "Price", "text", 3),
            ],
        }
    ]


def upgrade() -> None:
    op.create_table(
        "training_form_configurations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("scope", sa.String(20), nullable=False, server_default="global"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(255)),
        sa.Column("published_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
    )
    op.create_index("ix_training_form_config_scope_active", "training_form_configurations", ["scope", "is_active"])

    op.create_table(
        "training_form_configuration_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("configuration_id", UUID(as_uuid=True), sa.ForeignKey("training_form_configurations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("sections", JSONB, nullable=False, server_default="[]"),
        sa.Column("created_by", sa.String(255)),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("published_at", sa.DateTime()),
        sa.UniqueConstraint("configuration_id", "version", name="uq_training_form_config_version"),
    )
    op.create_index("ix_training_form_configuration_versions_configuration_id", "training_form_configuration_versions", ["configuration_id"])
    op.create_index("ix_training_form_version_status", "training_form_configuration_versions", ["configuration_id", "status"])

    op.create_table(
        "training_form_assignments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("configuration_id", UUID(as_uuid=True), sa.ForeignKey("training_form_configurations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("enterprise_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", sa.String(255)),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", name="uq_training_form_assignment_tenant"),
    )
    op.create_index("ix_training_form_assignments_configuration_id", "training_form_assignments", ["configuration_id"])
    op.create_index("ix_training_form_assignments_tenant_id", "training_form_assignments", ["tenant_id"])

    op.create_table(
        "training_form_audits",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("configuration_id", UUID(as_uuid=True), sa.ForeignKey("training_form_configurations.id", ondelete="CASCADE"), nullable=True),
        sa.Column("version_id", UUID(as_uuid=True), sa.ForeignKey("training_form_configuration_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_id", sa.String(255)),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("before", JSONB),
        sa.Column("after", JSONB),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_training_form_audits_configuration_id", "training_form_audits", ["configuration_id"])

    op.add_column("trainings", sa.Column("custom_values", JSONB, nullable=True, server_default="[]"))
    op.add_column("trainings", sa.Column("form_configuration_id", UUID(as_uuid=True), nullable=True))
    op.add_column("trainings", sa.Column("form_configuration_version_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_trainings_form_configuration_id",
        "trainings",
        "training_form_configurations",
        ["form_configuration_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_trainings_form_configuration_version_id",
        "trainings",
        "training_form_configuration_versions",
        ["form_configuration_version_id"],
        ["id"],
    )
    op.create_index("ix_trainings_form_configuration_id", "trainings", ["form_configuration_id"])
    op.create_index("ix_trainings_form_configuration_version_id", "trainings", ["form_configuration_version_id"])

    sections = _seed_sections()
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            INSERT INTO training_form_configurations
            (id, name, description, scope, status, is_active, current_version, created_by, published_at, created_at, updated_at)
            VALUES
            (:id, :name, :description, :scope, :status, :is_active, :current_version, :created_by, now(), now(), now())
            """
        ),
        {
            "id": DEFAULT_CONFIGURATION_ID,
            "name": "Default Training Form (Global)",
            "description": "Seeded Super Admin global Training form with title, category, and price.",
            "scope": "global",
            "status": "published",
            "is_active": True,
            "current_version": 1,
            "created_by": "system",
        },
    )
    conn.execute(
        sa.text(
            """
            INSERT INTO training_form_configuration_versions
            (id, configuration_id, version, status, sections, created_by, published_at, created_at)
            VALUES
            (:id, :configuration_id, :version, :status, CAST(:sections AS jsonb), :created_by, now(), now())
            """
        ),
        {
            "id": DEFAULT_VERSION_ID,
            "configuration_id": DEFAULT_CONFIGURATION_ID,
            "version": 1,
            "status": "published",
            "sections": json.dumps(sections),
            "created_by": "system",
        },
    )


def downgrade() -> None:
    op.drop_index("ix_trainings_form_configuration_version_id", table_name="trainings")
    op.drop_index("ix_trainings_form_configuration_id", table_name="trainings")
    op.drop_constraint("fk_trainings_form_configuration_version_id", "trainings", type_="foreignkey")
    op.drop_constraint("fk_trainings_form_configuration_id", "trainings", type_="foreignkey")
    op.drop_column("trainings", "form_configuration_version_id")
    op.drop_column("trainings", "form_configuration_id")
    op.drop_column("trainings", "custom_values")
    op.drop_table("training_form_audits")
    op.drop_table("training_form_assignments")
    op.drop_table("training_form_configuration_versions")
    op.drop_index("ix_training_form_config_scope_active", table_name="training_form_configurations")
    op.drop_table("training_form_configurations")
