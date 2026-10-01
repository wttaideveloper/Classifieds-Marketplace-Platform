"""add event_registration_options table and event_orders subtotal columns

Phase 2.8 (paid Meals + Accommodation): upgrades Meals/Accommodation from free informational selections into
real, capacity-controlled, priced options.

New table ``event_registration_options`` — one immutable row per purchased/selected meal or accommodation
option, snapshotting its name/price/currency at the moment of purchase (so a later price change on the Event
never rewrites historical orders). FK'd to ``event_registrations`` (every registration path already creates one,
free or paid) with ON DELETE CASCADE, and nullable FK to ``event_orders`` (ON DELETE SET NULL — a free-only
selection has no order) for direct traceability when the purchase was paid.

``event_orders`` gains three nullable subtotal columns (``ticket_subtotal``, ``meal_subtotal``,
``accommodation_subtotal``) that break the existing ``amount`` total down for audit/quote display. ``amount``
itself is untouched and remains authoritative — these are additive, informational columns. Existing rows are
backfilled (``ticket_subtotal = amount``, the other two = '0') purely for reporting consistency; no existing
order's payable amount changes.

Deliberately additive and behaviour-neutral for every event/order that predates this migration: no existing
column is altered, no existing row's `events.meals`/`events.accommodation`/`amount` is touched. Meal/accommodation
option pricing/capacity/purchase-window fields live inside the existing `events.meals`/`events.accommodation`
JSONB (no schema change needed there — see app/utils/event_meals.py, event_accommodation.py) and default to
"free, unlimited, always open" for every option that predates this feature.

Migration graph note: chained onto ``070237a7c1cf`` (the current event-domain head, Phase 2.2's Event Type
table), advancing that head instead of creating or merging one (the project's other heads are untouched;
deployments already run ``alembic upgrade heads``).

Revision ID: c4d8e9f1a2b3
Revises: 070237a7c1cf
Create Date: 2026-09-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c4d8e9f1a2b3'
down_revision: Union[str, None] = '070237a7c1cf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('event_orders', sa.Column('ticket_subtotal', sa.String(length=50), nullable=True))
    op.add_column('event_orders', sa.Column('meal_subtotal', sa.String(length=50), nullable=True))
    op.add_column('event_orders', sa.Column('accommodation_subtotal', sa.String(length=50), nullable=True))
    # Backfill for reporting consistency only — `amount` (the authoritative total) is never touched.
    op.execute(
        "UPDATE event_orders SET ticket_subtotal = amount, meal_subtotal = '0', accommodation_subtotal = '0' "
        "WHERE ticket_subtotal IS NULL"
    )

    op.create_table(
        'event_registration_options',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('event_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('events.id', ondelete='CASCADE'), nullable=False),
        sa.Column(
            'registration_id', postgresql.UUID(as_uuid=True),
            sa.ForeignKey('event_registrations.id', ondelete='CASCADE'), nullable=False,
        ),
        sa.Column('order_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('event_orders.id', ondelete='SET NULL'), nullable=True),
        sa.Column('option_type', sa.String(length=20), nullable=False),
        sa.Column('option_id', sa.String(length=64), nullable=False),
        sa.Column('option_name', sa.String(length=255), nullable=False),
        sa.Column('unit_price', sa.String(length=50), nullable=False, server_default='0'),
        sa.Column('currency', sa.String(length=10), nullable=False, server_default='INR'),
        sa.Column('quantity', sa.String(length=20), nullable=False, server_default='1'),
        sa.Column('line_total', sa.String(length=50), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_event_registration_options_event_id', 'event_registration_options', ['event_id'])
    op.create_index('ix_event_registration_options_registration_id', 'event_registration_options', ['registration_id'])
    op.create_index('ix_event_registration_options_order_id', 'event_registration_options', ['order_id'])
    op.create_index(
        'ix_event_registration_options_event_option',
        'event_registration_options', ['event_id', 'option_type', 'option_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_event_registration_options_event_option', table_name='event_registration_options')
    op.drop_index('ix_event_registration_options_order_id', table_name='event_registration_options')
    op.drop_index('ix_event_registration_options_registration_id', table_name='event_registration_options')
    op.drop_index('ix_event_registration_options_event_id', table_name='event_registration_options')
    op.drop_table('event_registration_options')
    op.drop_column('event_orders', 'accommodation_subtotal')
    op.drop_column('event_orders', 'meal_subtotal')
    op.drop_column('event_orders', 'ticket_subtotal')
