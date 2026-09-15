"""Event template provenance and incomplete template drafts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "w4x5y6z7a8b9"
down_revision = "v3w4x5y6z7a8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("event_templates", sa.Column("configuration_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("event_templates", sa.Column("configuration_version_id", postgresql.UUID(as_uuid=True), nullable=True))
    # Preserve provenance already embedded in legacy template JSON without guessing.
    op.execute("""
        UPDATE event_templates SET
            configuration_id = CASE WHEN template_data->>'form_configuration_id'
                ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                THEN (template_data->>'form_configuration_id')::uuid END,
            configuration_version_id = CASE WHEN template_data->>'form_configuration_version_id'
                ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                THEN (template_data->>'form_configuration_version_id')::uuid END
    """)
    for column in ("title", "category", "start_date", "end_date"):
        op.alter_column("events", column, nullable=True)


def downgrade():
    # Deliberately fails if incomplete drafts remain; never invent values or delete drafts.
    for column in ("title", "category", "start_date", "end_date"):
        op.alter_column("events", column, nullable=False)
    op.drop_column("event_templates", "configuration_version_id")
    op.drop_column("event_templates", "configuration_id")
