"""delete a conversation per participant

DELETE /conversations/{id} removes a chat from the caller's inbox only. The state lives on each
conversation_participants row (is_deleted, deleted_at): the other participant keeps their chat, and the
conversation, its messages and its history stay for them and for admins. deleted_at is also the point before which
the person who deleted it no longer sees messages, even if the chat later comes back because a new message arrived.

Existing rows start as not deleted.

Revision ID: d3a8f1c6e9b2
Revises: b6d1f3a8c2e4
Create Date: 2026-10-12 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = 'd3a8f1c6e9b2'
down_revision = 'b6d1f3a8c2e4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'conversation_participants',
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )
    op.add_column('conversation_participants', sa.Column('deleted_at', sa.DateTime(), nullable=True))
    op.create_index(
        'ix_conversation_participants_user_deleted',
        'conversation_participants',
        ['user_id', 'is_deleted'],
    )


def downgrade():
    op.drop_index('ix_conversation_participants_user_deleted', table_name='conversation_participants')
    op.drop_column('conversation_participants', 'deleted_at')
    op.drop_column('conversation_participants', 'is_deleted')
