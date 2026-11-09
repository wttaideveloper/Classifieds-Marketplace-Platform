"""Event Discussions: messages, moderation history, members and nudge bookkeeping.

Revision ID: i6e1a4d9f3c5
Revises: h5d0f3c8e2b4
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "i6e1a4d9f3c5"
down_revision = "h5d0f3c8e2b4"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade():
    op.create_table(
        "event_discussion_messages",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("event_id", UUID, sa.ForeignKey("events.id"), nullable=False),
        sa.Column("tenant_id", UUID),
        sa.Column("parent_id", UUID, sa.ForeignKey("event_discussion_messages.id")),
        sa.Column("root_id", UUID),
        sa.Column("author_id", sa.String(64), nullable=False),
        sa.Column("author_name", sa.String(255)),
        sa.Column("author_role", sa.String(20), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_announcement", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending_moderation"),
        sa.Column("is_appeal", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("moderation_label", sa.String(10)),
        sa.Column("moderation_reason", sa.String(500)),
        sa.Column("moderation_provider", sa.String(30)),
        sa.Column("moderation_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("decided_by", sa.String(64)),
        sa.Column("decided_by_name", sa.String(255)),
        sa.Column("decided_at", sa.DateTime()),
        sa.Column("decision_note", sa.String(500)),
        sa.Column("recipient_id", sa.String(64)),
        sa.Column("recipient_read_at", sa.DateTime()),
        sa.Column("nudged_at", sa.DateTime()),
        sa.Column("published_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for col in ("event_id", "tenant_id", "parent_id", "root_id", "author_id", "status", "recipient_id", "published_at", "created_at"):
        op.create_index(f"ix_event_discussion_messages_{col}", "event_discussion_messages", [col])
    op.create_index("ix_event_discussion_event_status", "event_discussion_messages", ["event_id", "status", "created_at"])

    op.create_table(
        "event_discussion_moderation",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("event_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID),
        sa.Column("message_id", UUID, nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("from_status", sa.String(20)),
        sa.Column("to_status", sa.String(20)),
        sa.Column("label", sa.String(10)),
        sa.Column("reason", sa.String(500)),
        sa.Column("actor_id", sa.String(64)),
        sa.Column("actor_name", sa.String(255)),
        sa.Column("actor_role", sa.String(20)),
        sa.Column("detail", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for col in ("event_id", "tenant_id", "message_id", "created_at"):
        op.create_index(f"ix_event_discussion_moderation_{col}", "event_discussion_moderation", [col])

    op.create_table(
        "event_discussion_members",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("event_id", UUID, sa.ForeignKey("events.id"), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255)),
        sa.Column("email", sa.String(255)),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("event_id", "user_id", name="uq_event_discussion_member"),
    )
    op.create_index("ix_event_discussion_members_event_id", "event_discussion_members", ["event_id"])
    op.create_index("ix_event_discussion_members_user_id", "event_discussion_members", ["user_id"])

    op.create_table(
        "event_discussion_nudges",
        sa.Column("event_id", UUID, primary_key=True),
        sa.Column("last_sent_at", sa.DateTime(), nullable=False),
        sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_table("event_discussion_nudges")
    op.drop_table("event_discussion_members")
    op.drop_table("event_discussion_moderation")
    op.drop_table("event_discussion_messages")
