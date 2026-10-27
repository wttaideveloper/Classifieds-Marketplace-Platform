"""Event wishlist.

Revision ID: e9c3a7b1d5f2
Revises: d4a8c2e6f901
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "e9c3a7b1d5f2"
down_revision = "d4a8c2e6f901"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "event_wishlist_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "event_id", name="uq_event_wishlist_user_event"),
    )
    op.create_index("ix_event_wishlist_items_user_id", "event_wishlist_items", ["user_id"])
    op.create_index("ix_event_wishlist_items_event_id", "event_wishlist_items", ["event_id"])


def downgrade():
    op.drop_index("ix_event_wishlist_items_event_id", table_name="event_wishlist_items")
    op.drop_index("ix_event_wishlist_items_user_id", table_name="event_wishlist_items")
    op.drop_table("event_wishlist_items")
