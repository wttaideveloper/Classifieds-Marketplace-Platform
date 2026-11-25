"""Dynamic attributes: photos and videos per attribute.

Revision ID: u1b4d7e0a2c6
Revises: t9e2a5c8f1b3d

dynamic_attributes.media   JSONB, nullable: [{"url", "type": "image" | "video", "name", "size", "mime"}]. Additive; existing rows keep null (no media).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "u1b4d7e0a2c6"
down_revision = "t9e2a5c8f1b3d"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("dynamic_attributes", sa.Column("media", postgresql.JSONB(), nullable=True))


def downgrade():
    op.drop_column("dynamic_attributes", "media")
