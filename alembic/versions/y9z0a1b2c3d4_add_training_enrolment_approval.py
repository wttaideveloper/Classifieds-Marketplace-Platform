"""add training enrolment approval, coupon, expiry, orders

Revision ID: y9z0a1b2c3d4
Revises: x8y9z0a1b2c3
Create Date: 2026-08-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'y9z0a1b2c3d4'
down_revision = 'x8y9z0a1b2c3'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('trainings', sa.Column('requires_approval', sa.Boolean, server_default='false'))
    op.add_column('trainings', sa.Column('access_duration_days', sa.String(20)))
    op.add_column('training_enrolments', sa.Column('coupon_code', sa.String(50)))
    op.add_column('training_enrolments', sa.Column('access_expires_at', sa.DateTime))
    # alter status to allow pending_approval
    op.create_table('training_orders',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('training_id', UUID(as_uuid=True), sa.ForeignKey('trainings.id'), nullable=False, index=True),
        sa.Column('participant_name', sa.String(255), nullable=False),
        sa.Column('participant_email', sa.String(255), nullable=False, index=True),
        sa.Column('quantity', sa.String(20), server_default='1'),
        sa.Column('amount', sa.String(50)),
        sa.Column('currency', sa.String(10), server_default='INR'),
        sa.Column('payment_status', sa.String(20), server_default='confirmed', index=True),
        sa.Column('status', sa.String(20), server_default='confirmed', index=True),
        sa.Column('coupon_code', sa.String(50)),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('now()')),
    )

def downgrade():
    op.drop_table('training_orders')
    op.drop_column('training_enrolments', 'access_expires_at')
    op.drop_column('training_enrolments', 'coupon_code')
    op.drop_column('trainings', 'access_duration_days')
    op.drop_column('trainings', 'requires_approval')
