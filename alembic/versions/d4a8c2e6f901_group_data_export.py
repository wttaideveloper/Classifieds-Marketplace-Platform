"""Durable, private group data export jobs.

Revision ID: d4a8c2e6f901
Revises: b3e7c1a9d5f4
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d4a8c2e6f901"
down_revision = "b3e7c1a9d5f4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("group_data_requests", sa.Column("processing_started_at", sa.DateTime(), nullable=True))
    op.add_column("group_data_requests", sa.Column("processing_token", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("group_data_requests", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("group_data_requests", sa.Column("error_code", sa.String(40), nullable=True))
    op.add_column("group_data_requests", sa.Column("export_json", sa.Text(), nullable=True))
    op.add_column("group_data_requests", sa.Column("expires_at", sa.DateTime(), nullable=True))
    op.create_index("ix_group_data_requests_status_created", "group_data_requests", ["status", "created_at"])


def downgrade():
    op.drop_index("ix_group_data_requests_status_created", table_name="group_data_requests")
    for name in ("expires_at", "export_json", "error_code", "attempts", "processing_token", "processing_started_at"):
        op.drop_column("group_data_requests", name)
