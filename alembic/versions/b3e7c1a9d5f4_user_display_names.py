"""remember the name each person goes by in chat

conversation list and detail responses carry `customer_name`. It was only ever resolved through the identity
service, which does not list a customer who is not a member of the provider's tenant, so the name came back null.
Each person's own sign-in carries their display name; it is now saved when they use chat, so the other side of the
conversation can see it. Existing conversations have no saved name until the person next uses chat (names the system
already holds from enrolments, addresses, orders and reviews are used in the meantime, without any migration).

Revision ID: b3e7c1a9d5f4
Revises: e5b2c7a9d1f3
Create Date: 2026-10-14 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'b3e7c1a9d5f4'
down_revision = 'e5b2c7a9d1f3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'user_display_names',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('display_name', sa.String(length=255), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False, server_default='token'),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table('user_display_names')
