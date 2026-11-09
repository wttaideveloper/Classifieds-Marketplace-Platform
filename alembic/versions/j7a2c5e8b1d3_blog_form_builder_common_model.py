"""Blog form builder on the shared form model: audit history, review status, and the stored definitions rewritten to the new field format.

Revision ID: j7a2c5e8b1d3
Revises: i6e1a4d9f3c5
"""
import logging

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "j7a2c5e8b1d3"
down_revision = "i6e1a4d9f3c5"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")
UUID = postgresql.UUID(as_uuid=True)


def upgrade():
    op.create_table(
        "cms_blog_form_audit",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("form_id", UUID, nullable=False),
        sa.Column("form_name", sa.String(120)),
        sa.Column("version", sa.Integer()),
        sa.Column("revision", sa.Integer()),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("detail", postgresql.JSONB()),
        sa.Column("actor_id", sa.String(100)),
        sa.Column("actor_name", sa.String(255)),
        sa.Column("actor_role", sa.String(20)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_cms_blog_form_audit_form_id", "cms_blog_form_audit", ["form_id"])
    op.create_index("ix_cms_blog_form_audit_action", "cms_blog_form_audit", ["action"])
    op.create_index("ix_cms_blog_form_audit_created_at", "cms_blog_form_audit", ["created_at"])
    op.add_column("cms_blog_forms", sa.Column("review_status", sa.String(10), nullable=False, server_default="draft"))
    op.add_column("cms_blog_forms", sa.Column("reviewed_revision", sa.Integer(), nullable=True))
    _rewrite_definitions()


def _rewrite_definitions():
    """Older definitions (text, textarea, select, multiselect, url, image, media_upload, checkbox ... and `visible` / `public|internal`) become the explicit
    types of the shared form model. The result is the same one the application computes when it reads an old definition, so this only saves it once.
    Published versions are rewritten too: their fields keep their keys, so saved post values still match. Not done in offline (--sql) mode."""
    if context.is_offline_mode():
        log.warning("offline mode: stored blog form definitions were NOT rewritten. Run this migration online, or the application converts them when it reads them.")
        return
    from app.services.blog_form_rules import is_current_format, upgrade_definition

    bind = op.get_bind()
    changed = unchanged = 0
    for table, column in (("cms_blog_forms", "draft_definition"), ("cms_blog_form_versions", "definition")):
        for row in bind.execute(sa.text(f"SELECT id, mode, {column} AS definition FROM {table}")).mappings():
            definition = row["definition"]
            if not definition or is_current_format(definition):
                unchanged += 1
                continue
            upgraded = upgrade_definition(definition, mode=row["mode"] or "configurable")
            if upgraded is definition or not is_current_format(upgraded):
                log.warning("%s %s could not be converted and was left as it is", table, row["id"])
                continue
            bind.execute(sa.text(f"UPDATE {table} SET {column} = CAST(:value AS JSONB) WHERE id = :id"), {"value": __import__("json").dumps(upgraded), "id": row["id"]})
            changed += 1
    log.info("blog form definitions: %d rewritten, %d already current", changed, unchanged)


def downgrade():
    # The stored definitions are not converted back: the application reads both formats.
    op.drop_column("cms_blog_forms", "reviewed_revision")
    op.drop_column("cms_blog_forms", "review_status")
    op.drop_table("cms_blog_form_audit")
