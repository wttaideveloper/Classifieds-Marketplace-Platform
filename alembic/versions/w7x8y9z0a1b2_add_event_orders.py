"""add event_orders

Revision ID: w7x8y9z0a1b2
Revises: v6w7x8y9z0a1
Create Date: 2026-08-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'w7x8y9z0a1b2'
down_revision = 'v6w7x8y9z0a1'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('event_orders',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('event_id', UUID(as_uuid=True), sa.ForeignKey('events.id'), nullable=False, index=True),
        sa.Column('participant_name', sa.String(255), nullable=False),
        sa.Column('participant_email', sa.String(255), nullable=False, index=True),
        sa.Column('ticket_type_id', sa.String(100), index=True),
        sa.Column('quantity', sa.String(20), server_default='1'),
        sa.Column('amount', sa.String(50)),
        sa.Column('currency', sa.String(10), server_default='INR'),
        sa.Column('payment_status', sa.String(20), server_default='confirmed', index=True),
        sa.Column('status', sa.String(20), server_default='confirmed', index=True),
        sa.Column('payment_provider', sa.String(50), server_default='marketplace'),
        sa.Column('refund_reason', sa.Text),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime, server_default=sa.text('now()')),
    )

def downgrade():
    op.drop_table('event_orders')
