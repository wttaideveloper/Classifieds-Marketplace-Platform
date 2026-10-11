"""Add events.default_ticket_type_id (backend-generated id of the Base Price default ticket).

Additive and nullable: no backfill. Existing Events keep NULL and gain no purchase option until someone
explicitly enables them (scripts/enable_default_ticket_type.py) or re-prices them through an update that
makes them eligible.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "e1f4a7c9b3d5"
down_revision = "c4d8e9f1a2b3"  # Phase 2.8: the head that carries the event-domain schema work
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("events", sa.Column("default_ticket_type_id", postgresql.UUID(as_uuid=True), nullable=True))


def downgrade():
    op.drop_column("events", "default_ticket_type_id")
