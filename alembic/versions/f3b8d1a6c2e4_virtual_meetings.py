"""Virtual meeting providers: platform switches, business connections, meeting links, audit.

Revision ID: f3b8d1a6c2e4
Revises: e9c3a7b1d5f2
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f3b8d1a6c2e4"
down_revision = "e9c3a7b1d5f2"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade():
    op.create_table(
        "meeting_provider_settings",
        sa.Column("provider", sa.String(20), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_by", UUID, nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "meeting_connections",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("account_id", sa.String(255)),
        sa.Column("account_email", sa.String(255)),
        sa.Column("account_name", sa.String(255)),
        sa.Column("access_token", sa.Text()),
        sa.Column("refresh_token", sa.Text()),
        sa.Column("token_expires_at", sa.DateTime()),
        sa.Column("scope", sa.Text()),
        sa.Column("connected_by", UUID, nullable=True),
        sa.Column("last_error", sa.String(255)),
        sa.Column("connected_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "provider", name="uq_meeting_connection_tenant_provider"),
    )
    op.create_index("ix_meeting_connections_tenant_id", "meeting_connections", ["tenant_id"])
    op.create_table(
        "meeting_merchant_settings",
        sa.Column("tenant_id", UUID, primary_key=True),
        sa.Column("default_provider", sa.String(20)),
        sa.Column("auto_generate", sa.Boolean(), nullable=False),
        sa.Column("updated_by", UUID, nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "meeting_links",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("resource_type", sa.String(30), nullable=False),
        sa.Column("resource_id", UUID, nullable=False),
        sa.Column("scope", sa.String(100), nullable=False, server_default=""),
        sa.Column("provider", sa.String(20)),
        sa.Column("auto_generate", sa.Boolean(), nullable=True),
        sa.Column("manual_link", sa.Text()),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("external_id", sa.String(255)),
        sa.Column("join_url", sa.Text()),
        sa.Column("passcode", sa.String(100)),
        sa.Column("dial_in", postgresql.JSONB()),
        sa.Column("connection_id", UUID, nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(500)),
        sa.Column("generated_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("resource_type", "resource_id", "scope", name="uq_meeting_link_resource"),
    )
    op.create_index("ix_meeting_links_tenant_id", "meeting_links", ["tenant_id"])
    op.create_index("ix_meeting_links_status", "meeting_links", ["status", "attempts"])
    op.create_table(
        "meeting_audit",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, nullable=True),
        sa.Column("resource_type", sa.String(30)),
        sa.Column("resource_id", UUID),
        sa.Column("provider", sa.String(20)),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("actor_id", UUID, nullable=True),
        sa.Column("details", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_meeting_audit_tenant_id", "meeting_audit", ["tenant_id"])
    op.create_index("ix_meeting_audit_created_at", "meeting_audit", ["created_at"])


def downgrade():
    for name in ("meeting_audit", "meeting_links", "meeting_merchant_settings", "meeting_connections", "meeting_provider_settings"):
        op.drop_table(name)
