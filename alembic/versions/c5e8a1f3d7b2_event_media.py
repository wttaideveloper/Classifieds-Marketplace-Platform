"""Event media assets (primary image, gallery, videos, documents)."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c5e8a1f3d7b2"
down_revision = "b9d4f2a6c8e1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "event_media",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enterprise_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("uploaded_by", sa.String(64), nullable=True),
        sa.Column("field", sa.String(20), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("ext", sa.String(10), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("client_ref", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("attached_at", sa.DateTime(), nullable=True),
        sa.Column("released_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_event_media_tenant_id", "event_media", ["tenant_id"])
    op.create_index("ix_event_media_uploader_client_ref", "event_media", ["uploaded_by", "client_ref"])


def downgrade():
    op.drop_table("event_media")
