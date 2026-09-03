"""add event form configuration domain and event form references

Revision ID: d0e1f2a3b4c5
Revises: b2c3d4e5f6a7, c9d0e1f2a3b4
Create Date: 2026-09-03
"""
from typing import Sequence, Union
import json
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, Sequence[str], None] = ("b2c3d4e5f6a7", "c9d0e1f2a3b4")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY_CONFIGURATION_ID = "00000000-0000-4000-8000-000000000001"
LEGACY_VERSION_ID = "00000000-0000-4000-8000-000000000002"


def _default_sections() -> list:
    """Inline minimal default sections for migration (mirrors registry seed)."""
    def f(core_key, label, renderer, pos, required=False):
        return {
            "id": str(uuid.uuid4()),
            "source": "core",
            "core_key": core_key,
            "stable_key": None,
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
        }

    def section(stable_key, label, position, fields):
        return {
            "id": str(uuid.uuid4()),
            "stable_key": stable_key,
            "label": label,
            "description": None,
            "position": position,
            "is_enabled": True,
            "fields": fields,
        }

    return [
        section("section_basic", "Basic Information", 1, [
            f("title", "Event Name", "text", 1, True),
            f("description", "Description", "textarea", 2, True),
            f("category", "Category", "select", 3, True),
            f("subcategory", "Subcategory", "select", 4),
            f("tags", "Tags", "tags", 5),
            f("organiser_name", "Organiser Name", "text", 6),
            f("organiser_contact", "Organiser Contact", "text", 7),
        ]),
        section("section_schedule", "Schedule", 2, [
            f("start_date", "Start Date & Time", "datetime", 1, True),
            f("end_date", "End Date & Time", "datetime", 2, True),
            f("duration_type", "Duration Type", "select", 3),
            f("time_zone", "Time Zone", "text", 4),
            f("registration_open_at", "Registration Opens", "datetime", 5),
            f("registration_close_at", "Registration Closes", "datetime", 6),
            f("registration_cutoff", "Registration Cutoff", "datetime", 7),
        ]),
        section("section_location", "Location & Host", 3, [
            f("delivery_mode", "Delivery Mode", "select", 1),
            f("location_id", "Enterprise Location", "select", 2),
            f("venue", "Venue", "venue", 3),
            f("meeting_provider", "Meeting Provider", "select", 4),
            f("meeting_link", "Meeting Link", "url", 5),
        ]),
        section("section_pricing", "Pricing & Tickets", 4, [
            f("price", "Price", "text", 1),
            f("currency", "Currency", "text", 2),
            f("ticket_types", "Ticket Types", "ticket_types", 3),
            f("capacity", "Capacity", "number", 4),
            f("min_participants", "Minimum Participants", "number", 5),
            f("max_participants", "Maximum Participants", "number", 6),
        ]),
        section("section_media", "Media", 5, [
            f("primary_image", "Primary Image", "url", 1),
            f("gallery_images", "Gallery Images", "media", 2),
            f("videos", "Videos", "media", 3),
            f("documents", "Documents", "media", 4),
        ]),
        section("section_additional", "Additional", 6, [
            f("sessions", "Sessions / Agenda", "sessions", 1),
            f("custom_fields", "Registration Questions", "registration_fields", 2),
        ]),
    ]


def upgrade() -> None:
    op.create_table(
        "event_form_configurations",
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
    op.create_index("ix_event_form_config_scope_active", "event_form_configurations", ["scope", "is_active"])

    op.create_table(
        "event_form_configuration_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("configuration_id", UUID(as_uuid=True), sa.ForeignKey("event_form_configurations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("sections", JSONB, nullable=False, server_default="[]"),
        sa.Column("created_by", sa.String(255)),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("published_at", sa.DateTime()),
        sa.UniqueConstraint("configuration_id", "version", name="uq_event_form_config_version"),
    )
    op.create_index("ix_event_form_configuration_versions_configuration_id", "event_form_configuration_versions", ["configuration_id"])
    op.create_index("ix_event_form_version_status", "event_form_configuration_versions", ["configuration_id", "status"])

    op.create_table(
        "event_form_assignments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("configuration_id", UUID(as_uuid=True), sa.ForeignKey("event_form_configurations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("enterprise_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", sa.String(255)),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", name="uq_event_form_assignment_tenant"),
    )
    op.create_index("ix_event_form_assignments_configuration_id", "event_form_assignments", ["configuration_id"])
    op.create_index("ix_event_form_assignments_tenant_id", "event_form_assignments", ["tenant_id"])

    op.create_table(
        "event_form_audits",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("configuration_id", UUID(as_uuid=True), sa.ForeignKey("event_form_configurations.id", ondelete="CASCADE"), nullable=True),
        sa.Column("version_id", UUID(as_uuid=True), sa.ForeignKey("event_form_configuration_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_id", sa.String(255)),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("before", JSONB),
        sa.Column("after", JSONB),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_event_form_audits_configuration_id", "event_form_audits", ["configuration_id"])

    op.add_column("events", sa.Column("custom_values", JSONB, nullable=True, server_default="[]"))
    op.add_column("events", sa.Column("form_configuration_id", UUID(as_uuid=True), nullable=True))
    op.add_column("events", sa.Column("form_configuration_version_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_events_form_configuration_id",
        "events",
        "event_form_configurations",
        ["form_configuration_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_events_form_configuration_version_id",
        "events",
        "event_form_configuration_versions",
        ["form_configuration_version_id"],
        ["id"],
    )
    op.create_index("ix_events_form_configuration_id", "events", ["form_configuration_id"])
    op.create_index("ix_events_form_configuration_version_id", "events", ["form_configuration_version_id"])

    sections = _default_sections()
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            INSERT INTO event_form_configurations
            (id, name, description, scope, status, is_active, current_version, created_by, published_at, created_at, updated_at)
            VALUES
            (:id, :name, :description, :scope, :status, :is_active, :current_version, :created_by, now(), now(), now())
            """
        ),
        {
            "id": LEGACY_CONFIGURATION_ID,
            "name": "Legacy / Default Event Form",
            "description": "Seeded default configuration matching the original Event create form. Used as global fallback and for legacy Events.",
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
            INSERT INTO event_form_configuration_versions
            (id, configuration_id, version, status, sections, created_by, published_at, created_at)
            VALUES
            (:id, :configuration_id, :version, :status, CAST(:sections AS jsonb), :created_by, now(), now())
            """
        ),
        {
            "id": LEGACY_VERSION_ID,
            "configuration_id": LEGACY_CONFIGURATION_ID,
            "version": 1,
            "status": "published",
            "sections": json.dumps(sections),
            "created_by": "system",
        },
    )


def downgrade() -> None:
    op.drop_index("ix_events_form_configuration_version_id", table_name="events")
    op.drop_index("ix_events_form_configuration_id", table_name="events")
    op.drop_constraint("fk_events_form_configuration_version_id", "events", type_="foreignkey")
    op.drop_constraint("fk_events_form_configuration_id", "events", type_="foreignkey")
    op.drop_column("events", "form_configuration_version_id")
    op.drop_column("events", "form_configuration_id")
    op.drop_column("events", "custom_values")
    op.drop_table("event_form_audits")
    op.drop_table("event_form_assignments")
    op.drop_table("event_form_configuration_versions")
    op.drop_index("ix_event_form_config_scope_active", table_name="event_form_configurations")
    op.drop_table("event_form_configurations")
