"""Blog files: soft delete and replace tracking (video uploads need no schema change: `purpose` is a string).

Revision ID: k8b3d6f9c2e5
Revises: j7a2c5e8b1d3
"""
from alembic import op
import sqlalchemy as sa

revision = "k8b3d6f9c2e5"
down_revision = "j7a2c5e8b1d3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("cms_blog_files", sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("cms_blog_files", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.add_column("cms_blog_files", sa.Column("replaced_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("cms_blog_files", "replaced_at")
    op.drop_column("cms_blog_files", "deleted_at")
    op.drop_column("cms_blog_files", "is_deleted")
