"""Form builder: indexes for the queries the form builder routes run.

Revision ID: m2d5f8b3a6c1
Revises: l9c4e7a1d2f6

What each one serves:
  cms_blog_form_audit (form_id, created_at)        the audit history of a form, newest first (GET /admin/forms/{id}/audit)
  cms_blog_forms (status, is_active, form_type)    choosing the active form for a kind of post, and the forms list filters
  cms_blog_posts (form_id, form_version)           how many posts use each version of a form (FORM_IN_USE, the versions list)
  cms_blog_files (tenant_id, purpose, created_at)  a business's uploads, newest first, filtered by kind (GET /uploads)
  event_form_audits / training_form_audits         the audit history of an Event or Training form configuration, newest first
Plain (non-concurrent) CREATE INDEX: it locks writes to the table while it builds. These tables are small; on a large cms_blog_posts run it in a quiet hour or create the index
CONCURRENTLY by hand first (the name below) and let this migration skip it.
"""
from alembic import op

revision = "m2d5f8b3a6c1"
down_revision = "l9c4e7a1d2f6"
branch_labels = None
depends_on = None

INDEXES = [
    ("ix_cms_blog_form_audit_form_created", "cms_blog_form_audit", ["form_id", "created_at"]),
    ("ix_cms_blog_forms_active_lookup", "cms_blog_forms", ["status", "is_active", "form_type"]),
    ("ix_cms_blog_posts_form_version", "cms_blog_posts", ["form_id", "form_version"]),
    ("ix_cms_blog_files_tenant_purpose_created", "cms_blog_files", ["tenant_id", "purpose", "created_at"]),
    ("ix_event_form_audits_config_created", "event_form_audits", ["configuration_id", "created_at"]),
    ("ix_training_form_audits_config_created", "training_form_audits", ["configuration_id", "created_at"]),
]


def upgrade():
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns, if_not_exists=True)


def downgrade():
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table, if_exists=True)
