"""History of review moderation: who approved, rejected, reset or deleted which review, and when."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a3c5e7f9b1d4"
down_revision = "f2b4d6a8c0e1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "review_moderation_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("module", sa.String(length=20), nullable=False),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_name", sa.String(length=255), nullable=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_role", sa.String(length=30), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_review_moderation_log_review_id", "review_moderation_log", ["review_id"])
    op.create_index("ix_review_moderation_log_item_id", "review_moderation_log", ["item_id"])
    op.create_index("ix_review_moderation_log_tenant_id", "review_moderation_log", ["tenant_id"])
    op.create_index("ix_review_moderation_log_module_created", "review_moderation_log", ["module", "created_at"])


def downgrade():
    op.drop_table("review_moderation_log")
