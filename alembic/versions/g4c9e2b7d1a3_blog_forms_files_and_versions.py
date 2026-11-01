"""Blogs and news: forms with versions and assignments, uploaded files, extra post fields, post version and revision snapshots.

Revision ID: g4c9e2b7d1a3
Revises: f3b8d1a6c2e4
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "g4c9e2b7d1a3"
down_revision = "f3b8d1a6c2e4"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()


def upgrade():
    op.create_table(
        "cms_blog_forms",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("name_key", sa.String(120), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("form_type", sa.String(8), nullable=False, server_default="both"),
        sa.Column("mode", sa.String(12), nullable=False, server_default="standard"),
        sa.Column("scope", sa.String(10), nullable=False, server_default="global"),
        sa.Column("status", sa.String(10), nullable=False, server_default="draft"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("draft_definition", JSONB, nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("has_unpublished_changes", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(100)),
        sa.Column("updated_by", sa.String(100)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_index("ix_cms_blog_forms_name_key", "cms_blog_forms", ["name_key"], unique=True)
    op.create_index("ix_cms_blog_forms_status", "cms_blog_forms", ["status"])
    op.create_index("ix_cms_blog_forms_is_active", "cms_blog_forms", ["is_active"])

    op.create_table(
        "cms_blog_form_versions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("form_id", UUID, sa.ForeignKey("cms_blog_forms.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("form_type", sa.String(8), nullable=False),
        sa.Column("mode", sa.String(12), nullable=False),
        sa.Column("definition", JSONB, nullable=False),
        sa.Column("note", sa.String(500)),
        sa.Column("published_by", sa.String(100)),
        sa.Column("published_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_cms_blog_form_versions_form_id", "cms_blog_form_versions", ["form_id"])
    op.create_index("uq_cms_blog_form_version", "cms_blog_form_versions", ["form_id", "version"], unique=True)

    op.create_table(
        "cms_blog_form_assignments",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("form_id", UUID, sa.ForeignKey("cms_blog_forms.id"), nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_cms_blog_form_assignments_form_id", "cms_blog_form_assignments", ["form_id"])
    op.create_index("ix_cms_blog_form_assignments_tenant_id", "cms_blog_form_assignments", ["tenant_id"])
    op.create_index("uq_cms_blog_form_assignment", "cms_blog_form_assignments", ["form_id", "tenant_id"], unique=True)

    op.create_table(
        "cms_blog_files",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, nullable=True),
        sa.Column("uploaded_by", sa.String(100), nullable=False),
        sa.Column("purpose", sa.String(12), nullable=False),
        sa.Column("stored_name", sa.String(120), nullable=False),
        sa.Column("original_name", sa.String(255)),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("sha256", sa.String(64)),
        sa.Column("scan_status", sa.String(12), nullable=False, server_default="skipped"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_cms_blog_files_tenant_id", "cms_blog_files", ["tenant_id"])
    op.create_index("ix_cms_blog_files_stored_name", "cms_blog_files", ["stored_name"], unique=True)

    op.add_column("cms_blog_posts", sa.Column("additional_images", JSONB, nullable=True))
    op.add_column("cms_blog_posts", sa.Column("reference_urls", JSONB, nullable=True))
    op.add_column("cms_blog_posts", sa.Column("supporting_documents", JSONB, nullable=True))
    op.add_column("cms_blog_posts", sa.Column("custom_values", JSONB, nullable=True))
    op.add_column("cms_blog_posts", sa.Column("form_id", UUID, nullable=True))
    op.add_column("cms_blog_posts", sa.Column("form_version", sa.Integer(), nullable=True))
    op.add_column("cms_blog_posts", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.create_index("ix_cms_blog_posts_form_id", "cms_blog_posts", ["form_id"])

    op.add_column("cms_blog_post_history", sa.Column("post_version", sa.Integer(), nullable=True))
    op.add_column("cms_blog_post_history", sa.Column("snapshot", JSONB, nullable=True))


def downgrade():
    op.drop_column("cms_blog_post_history", "snapshot")
    op.drop_column("cms_blog_post_history", "post_version")
    op.drop_index("ix_cms_blog_posts_form_id", table_name="cms_blog_posts")
    for column in ("version", "form_version", "form_id", "custom_values", "supporting_documents", "reference_urls", "additional_images"):
        op.drop_column("cms_blog_posts", column)
    op.drop_table("cms_blog_files")
    op.drop_table("cms_blog_form_assignments")
    op.drop_table("cms_blog_form_versions")
    op.drop_table("cms_blog_forms")
