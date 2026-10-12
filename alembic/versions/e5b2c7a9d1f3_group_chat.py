"""group chat

Adds what group chat needs (docs/group-chat-api.md):

* conversations: group_kind, description, avatar_url, admins_only_posting, closed_at, closed_reason,
  retention_started_at, legal_hold, legal_hold_reason
* conversation_participants: membership_status (invited / active / declined / removed / left) and the group state of
  a member: role, kind, display name, consent / invitation times, history cut-off, mutes, pin
* messages: reply, mentions, edit history, retract / admin hide, pin
* new tables: group_audit_log, group_consents, group_reports, group_message_reactions, verified_providers,
  group_clinical_record_entries, group_data_requests

Every existing row keeps working unchanged: all current participants become membership_status = 'active', and the
new message / conversation columns are empty or false.

Revision ID: e5b2c7a9d1f3
Revises: d3a8f1c6e9b2
Create Date: 2026-10-13 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'e5b2c7a9d1f3'
down_revision = 'd3a8f1c6e9b2'
branch_labels = None
depends_on = None


def _json():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def _uuid():
    return postgresql.UUID(as_uuid=True)


def upgrade():
    # --- conversations
    op.add_column('conversations', sa.Column('group_kind', sa.String(20), nullable=True))
    op.add_column('conversations', sa.Column('description', sa.String(500), nullable=True))
    op.add_column('conversations', sa.Column('avatar_url', sa.String(500), nullable=True))
    op.add_column('conversations', sa.Column('admins_only_posting', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('conversations', sa.Column('closed_at', sa.DateTime(), nullable=True))
    op.add_column('conversations', sa.Column('closed_reason', sa.String(255), nullable=True))
    op.add_column('conversations', sa.Column('retention_started_at', sa.DateTime(), nullable=True))
    op.add_column('conversations', sa.Column('legal_hold', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('conversations', sa.Column('legal_hold_reason', sa.String(500), nullable=True))

    # --- conversation_participants
    cp = 'conversation_participants'
    op.add_column(cp, sa.Column('membership_status', sa.String(12), nullable=False, server_default='active'))
    op.add_column(cp, sa.Column('group_role', sa.String(10), nullable=True))
    op.add_column(cp, sa.Column('member_kind', sa.String(10), nullable=True))
    op.add_column(cp, sa.Column('display_name', sa.String(120), nullable=True))
    op.add_column(cp, sa.Column('is_minor', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column(cp, sa.Column('invited_by', _uuid(), nullable=True))
    op.add_column(cp, sa.Column('invited_at', sa.DateTime(), nullable=True))
    op.add_column(cp, sa.Column('responded_at', sa.DateTime(), nullable=True))
    op.add_column(cp, sa.Column('left_at', sa.DateTime(), nullable=True))
    op.add_column(cp, sa.Column('removed_by', _uuid(), nullable=True))
    op.add_column(cp, sa.Column('removed_reason', sa.String(255), nullable=True))
    op.add_column(cp, sa.Column('history_from', sa.DateTime(), nullable=True))
    op.add_column(cp, sa.Column('history_sharing_consent_at', sa.DateTime(), nullable=True))
    op.add_column(cp, sa.Column('history_shared_by', _uuid(), nullable=True))
    op.add_column(cp, sa.Column('muted', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column(cp, sa.Column('muted_until', sa.DateTime(), nullable=True))
    op.add_column(cp, sa.Column('pinned', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column(cp, sa.Column('posting_muted', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column(cp, sa.Column('posting_muted_until', sa.DateTime(), nullable=True))
    op.add_column(cp, sa.Column('posting_muted_by', _uuid(), nullable=True))
    op.add_column(cp, sa.Column('posting_muted_reason', sa.String(255), nullable=True))
    op.create_index('ix_conversation_participants_membership_status', cp, ['membership_status'])

    # --- messages
    op.add_column('messages', sa.Column('reply_to_id', _uuid(), nullable=True))
    op.add_column('messages', sa.Column('mentions', _json(), nullable=True))
    op.add_column('messages', sa.Column('mentions_all', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('messages', sa.Column('edit_history', _json(), nullable=True))
    op.add_column('messages', sa.Column('hidden_kind', sa.String(12), nullable=True))
    op.add_column('messages', sa.Column('hidden_by', _uuid(), nullable=True))
    op.add_column('messages', sa.Column('hidden_reason', sa.String(255), nullable=True))
    op.add_column('messages', sa.Column('hidden_at', sa.DateTime(), nullable=True))
    op.add_column('messages', sa.Column('pinned_at', sa.DateTime(), nullable=True))
    op.add_column('messages', sa.Column('pinned_by', _uuid(), nullable=True))
    op.create_index('ix_messages_reply_to_id', 'messages', ['reply_to_id'])

    # --- new tables
    op.create_table(
        'group_audit_log',
        sa.Column('id', _uuid(), primary_key=True),
        sa.Column('conversation_id', _uuid(), nullable=False),
        sa.Column('tenant_id', _uuid(), nullable=True),
        sa.Column('actor_id', _uuid(), nullable=True),
        sa.Column('actor_role', sa.String(20), nullable=True),
        sa.Column('action', sa.String(40), nullable=False),
        sa.Column('target_user_id', _uuid(), nullable=True),
        sa.Column('message_id', _uuid(), nullable=True),
        sa.Column('reason', sa.String(500), nullable=True),
        sa.Column('details', _json(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_group_audit_log_conversation_id', 'group_audit_log', ['conversation_id'])
    op.create_index('ix_group_audit_log_tenant_id', 'group_audit_log', ['tenant_id'])
    op.create_index('ix_group_audit_log_action', 'group_audit_log', ['action'])
    op.create_index('ix_group_audit_log_created_at', 'group_audit_log', ['created_at'])
    op.create_index('ix_group_audit_conversation_created', 'group_audit_log', ['conversation_id', 'created_at'])

    op.create_table(
        'group_consents',
        sa.Column('id', _uuid(), primary_key=True),
        sa.Column('conversation_id', _uuid(), nullable=False),
        sa.Column('user_id', _uuid(), nullable=False),
        sa.Column('granted_at', sa.DateTime(), nullable=False),
        sa.Column('withdrawn_at', sa.DateTime(), nullable=True),
        sa.Column('is_minor', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('guardian_name', sa.String(255), nullable=True),
        sa.Column('guardian_relationship', sa.String(80), nullable=True),
        sa.Column('notice', _json(), nullable=True),
    )
    op.create_index('ix_group_consents_conversation_id', 'group_consents', ['conversation_id'])
    op.create_index('ix_group_consents_user_id', 'group_consents', ['user_id'])

    op.create_table(
        'group_reports',
        sa.Column('id', _uuid(), primary_key=True),
        sa.Column('conversation_id', _uuid(), nullable=False),
        sa.Column('tenant_id', _uuid(), nullable=True),
        sa.Column('message_id', _uuid(), nullable=True),
        sa.Column('reporter_id', _uuid(), nullable=False),
        sa.Column('reason', sa.String(40), nullable=False),
        sa.Column('details', sa.String(1000), nullable=True),
        sa.Column('status', sa.String(12), nullable=False, server_default='open'),
        sa.Column('resolution_note', sa.String(1000), nullable=True),
        sa.Column('resolved_by', _uuid(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_group_reports_conversation_id', 'group_reports', ['conversation_id'])
    op.create_index('ix_group_reports_tenant_id', 'group_reports', ['tenant_id'])
    op.create_index('ix_group_reports_reporter_id', 'group_reports', ['reporter_id'])
    op.create_index('ix_group_reports_status', 'group_reports', ['status'])
    op.create_index('ix_group_reports_created_at', 'group_reports', ['created_at'])

    op.create_table(
        'group_message_reactions',
        sa.Column('id', _uuid(), primary_key=True),
        sa.Column('message_id', _uuid(), nullable=False),
        sa.Column('conversation_id', _uuid(), nullable=False),
        sa.Column('user_id', _uuid(), nullable=False),
        sa.Column('emoji', sa.String(16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('message_id', 'user_id', 'emoji', name='uq_group_reaction_message_user_emoji'),
    )
    op.create_index('ix_group_message_reactions_message_id', 'group_message_reactions', ['message_id'])
    op.create_index('ix_group_message_reactions_conversation_id', 'group_message_reactions', ['conversation_id'])

    op.create_table(
        'verified_providers',
        sa.Column('id', _uuid(), primary_key=True),
        sa.Column('user_id', _uuid(), nullable=False, unique=True),
        sa.Column('tenant_id', _uuid(), nullable=True),
        sa.Column('registration_number', sa.String(80), nullable=True),
        sa.Column('registration_council', sa.String(120), nullable=True),
        sa.Column('status', sa.String(10), nullable=False, server_default='verified'),
        sa.Column('verified_by', _uuid(), nullable=True),
        sa.Column('verified_at', sa.DateTime(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('notes', sa.String(500), nullable=True),
    )
    op.create_index('ix_verified_providers_user_id', 'verified_providers', ['user_id'])
    op.create_index('ix_verified_providers_tenant_id', 'verified_providers', ['tenant_id'])
    op.create_index('ix_verified_providers_status', 'verified_providers', ['status'])

    op.create_table(
        'group_clinical_record_entries',
        sa.Column('id', _uuid(), primary_key=True),
        sa.Column('conversation_id', _uuid(), nullable=False),
        sa.Column('tenant_id', _uuid(), nullable=True),
        sa.Column('message_id', _uuid(), nullable=False),
        sa.Column('patient_user_id', _uuid(), nullable=False),
        sa.Column('saved_by', _uuid(), nullable=False),
        sa.Column('sender_id', _uuid(), nullable=False),
        sa.Column('content_snapshot', sa.Text(), nullable=True),
        sa.Column('attachment_id', _uuid(), nullable=True),
        sa.Column('message_created_at', sa.DateTime(), nullable=False),
        sa.Column('note', sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('message_id', 'patient_user_id', name='uq_group_record_message_patient'),
    )
    op.create_index('ix_group_clinical_record_entries_conversation_id', 'group_clinical_record_entries', ['conversation_id'])
    op.create_index('ix_group_clinical_record_entries_tenant_id', 'group_clinical_record_entries', ['tenant_id'])
    op.create_index('ix_group_clinical_record_entries_message_id', 'group_clinical_record_entries', ['message_id'])
    op.create_index('ix_group_clinical_record_entries_patient_user_id', 'group_clinical_record_entries', ['patient_user_id'])

    op.create_table(
        'group_data_requests',
        sa.Column('id', _uuid(), primary_key=True),
        sa.Column('user_id', _uuid(), nullable=False),
        sa.Column('conversation_id', _uuid(), nullable=True),
        sa.Column('request_type', sa.String(20), nullable=False, server_default='copy'),
        sa.Column('status', sa.String(12), nullable=False, server_default='received'),
        sa.Column('note', sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_group_data_requests_user_id', 'group_data_requests', ['user_id'])


def downgrade():
    for table in (
        'group_data_requests', 'group_clinical_record_entries', 'verified_providers',
        'group_message_reactions', 'group_reports', 'group_consents', 'group_audit_log',
    ):
        op.drop_table(table)

    op.drop_index('ix_messages_reply_to_id', table_name='messages')
    for column in (
        'pinned_by', 'pinned_at', 'hidden_at', 'hidden_reason', 'hidden_by', 'hidden_kind', 'edit_history',
        'mentions_all', 'mentions', 'reply_to_id',
    ):
        op.drop_column('messages', column)

    op.drop_index('ix_conversation_participants_membership_status', table_name='conversation_participants')
    for column in (
        'posting_muted_reason', 'posting_muted_by', 'posting_muted_until', 'posting_muted', 'pinned', 'muted_until',
        'muted', 'history_shared_by', 'history_sharing_consent_at', 'history_from', 'removed_reason', 'removed_by',
        'left_at', 'responded_at', 'invited_at', 'invited_by', 'is_minor', 'display_name', 'member_kind',
        'group_role', 'membership_status',
    ):
        op.drop_column('conversation_participants', column)

    for column in (
        'legal_hold_reason', 'legal_hold', 'retention_started_at', 'closed_reason', 'closed_at',
        'admins_only_posting', 'avatar_url', 'description', 'group_kind',
    ):
        op.drop_column('conversations', column)
