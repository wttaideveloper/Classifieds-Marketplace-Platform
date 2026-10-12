"""show who moderated a review by name in the moderation history

GET /reviews/audit returned only actor_user_id and actor_role, so the apps had to look every moderator up. The
display name is now saved with each history row when the action happens (never an e-mail address), and the API
returns it as actor_name.

Existing rows have no saved name (NULL). The API fills it in from the identity service when the history is read,
so older rows show a name too whenever one can be found.

Revision ID: a9c4e1b7d3f2
Revises: c7e2a9d4b1f6
Create Date: 2026-10-13 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = 'a9c4e1b7d3f2'
down_revision = 'c7e2a9d4b1f6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('review_moderation_log', sa.Column('actor_name', sa.String(length=255), nullable=True))


def downgrade():
    op.drop_column('review_moderation_log', 'actor_name')
