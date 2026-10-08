"""archive state per conversation participant

Archiving used to be one flag on the conversation (status = 'archived'), so when the provider archived a
chat the customer lost it too. The state now lives on each conversation_participants row
(is_archived, archived_at), so each person archives for themselves only.

Existing data: a conversation that was archived (status = 'archived') had no per-person state, so every
participant of it is marked archived (they all saw it archived before, so nothing changes for them) and the
conversation goes back to 'open'. The status it had before it was archived was not kept, so a chat that was
closed before being archived comes back as open. An assigned provider who has no participant row gets one so
that their archived state is kept.

Revision ID: b6d1f3a8c2e4
Revises: a2b3c4d5e6f7
Create Date: 2026-10-08 00:00:00.000000

"""
import uuid
from datetime import datetime

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = 'b6d1f3a8c2e4'
down_revision = 'a2b3c4d5e6f7'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'conversation_participants',
        sa.Column('is_archived', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )
    op.add_column('conversation_participants', sa.Column('archived_at', sa.DateTime(), nullable=True))
    op.create_index(
        'ix_conversation_participants_user_archived',
        'conversation_participants',
        ['user_id', 'is_archived'],
    )

    bind = op.get_bind()
    archived = bind.execute(
        sa.text("SELECT id, assigned_provider_id, archived_at, updated_at FROM conversations WHERE status = 'archived'")
    ).fetchall()
    for conversation_id, provider_id, archived_at, updated_at in archived:
        when = archived_at or updated_at or datetime.utcnow()
        bind.execute(
            sa.text(
                "UPDATE conversation_participants SET is_archived = true, archived_at = :when "
                "WHERE conversation_id = :cid"
            ),
            {"when": when, "cid": conversation_id},
        )
        if provider_id is not None:
            has_row = bind.execute(
                sa.text("SELECT 1 FROM conversation_participants WHERE conversation_id = :cid AND user_id = :uid"),
                {"cid": conversation_id, "uid": provider_id},
            ).first()
            if has_row is None:
                bind.execute(
                    sa.text(
                        "INSERT INTO conversation_participants "
                        "(id, conversation_id, user_id, role, joined_at, is_archived, archived_at) "
                        "VALUES (:id, :cid, :uid, 'provider', :when, true, :when)"
                    ),
                    {"id": str(uuid.uuid4()), "cid": conversation_id, "uid": provider_id, "when": when},
                )
    bind.execute(sa.text("UPDATE conversations SET status = 'open' WHERE status = 'archived'"))


def downgrade():
    bind = op.get_bind()
    # A chat archived by anyone goes back to the old single flag.
    bind.execute(
        sa.text(
            "UPDATE conversations SET status = 'archived' WHERE id IN ("
            "SELECT conversation_id FROM conversation_participants WHERE is_archived = true)"
        )
    )
    op.drop_index('ix_conversation_participants_user_archived', table_name='conversation_participants')
    op.drop_column('conversation_participants', 'archived_at')
    op.drop_column('conversation_participants', 'is_archived')
