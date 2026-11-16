"""Record the outstanding stock debit per order line."""
from alembic import op
import sqlalchemy as sa

revision = "q6f182cbd701"
down_revision = "q6b9d2f5a8c3"
branch_labels = depends_on = None

def upgrade():
    op.add_column("order_items", sa.Column("stock_debited", sa.Integer(), server_default="0", nullable=False))

def downgrade():
    op.drop_column("order_items", "stock_debited")
