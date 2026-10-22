"""eBook lessons: structured pages (draft and published copies), approval trail, reading progress, bookmarks, notes, and image assets

Adds media_assets (uploaded pictures with their size, pixel dimensions, checksum and scan status), training_ebooks (one per eBook lesson, with the
draft, the published copy and the approval state), training_ebook_versions (copies kept when an eBook is submitted, approved or published),
training_ebook_audit (every change), and the learner tables training_ebook_progress, training_ebook_bookmarks and training_ebook_notes.

Pictures uploaded before this migration have no asset record; upload them again to use them in an eBook.

Revision ID: c8f4a2d6b9e1
Revises: b7e3d9a1c5f2
Create Date: 2026-10-23 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c8f4a2d6b9e1"
down_revision = "b7e3d9a1c5f2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "media_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("stored_name", sa.String(length=255), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("scan_status", sa.String(length=12), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(*['stored_name']),
    )
    op.create_index("ix_media_assets_owner_user_id", "media_assets", ['owner_user_id'], unique=False)
    op.create_index("ix_media_assets_sha256", "media_assets", ['sha256'], unique=False)
    op.create_index("ix_media_assets_tenant_id", "media_assets", ['tenant_id'], unique=False)
    op.create_table(
        "training_ebook_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("ebook_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("training_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lesson_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.String(length=64), nullable=True),
        sa.Column("actor_name", sa.String(length=255), nullable=True),
        sa.Column("actor_role", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_training_ebook_audit_created_at", "training_ebook_audit", ['created_at'], unique=False)
    op.create_index("ix_training_ebook_audit_ebook_id", "training_ebook_audit", ['ebook_id'], unique=False)
    op.create_index("ix_training_ebook_audit_training_id", "training_ebook_audit", ['training_id'], unique=False)
    op.create_table(
        "training_ebook_bookmarks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("training_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lesson_id", sa.String(length=64), nullable=False),
        sa.Column("participant_email", sa.String(length=255), nullable=False),
        sa.Column("page_id", sa.String(length=64), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("scroll_position", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_training_ebook_bookmarks_lookup", "training_ebook_bookmarks", ['training_id', 'lesson_id', 'participant_email'], unique=False)
    op.create_index("ix_training_ebook_bookmarks_participant_email", "training_ebook_bookmarks", ['participant_email'], unique=False)
    op.create_index("ix_training_ebook_bookmarks_training_id", "training_ebook_bookmarks", ['training_id'], unique=False)
    op.create_table(
        "training_ebook_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("training_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lesson_id", sa.String(length=64), nullable=False),
        sa.Column("participant_email", sa.String(length=255), nullable=False),
        sa.Column("page_id", sa.String(length=64), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("color", sa.String(length=20), nullable=True),
        sa.Column("scroll_position", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_training_ebook_notes_lookup", "training_ebook_notes", ['training_id', 'lesson_id', 'participant_email'], unique=False)
    op.create_index("ix_training_ebook_notes_participant_email", "training_ebook_notes", ['participant_email'], unique=False)
    op.create_index("ix_training_ebook_notes_training_id", "training_ebook_notes", ['training_id'], unique=False)
    op.create_table(
        "training_ebook_progress",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("training_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lesson_id", sa.String(length=64), nullable=False),
        sa.Column("participant_email", sa.String(length=255), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_page_id", sa.String(length=64), nullable=True),
        sa.Column("last_page_number", sa.Integer(), nullable=True),
        sa.Column("last_scroll_position", sa.Float(), nullable=True),
        sa.Column("progress_percent", sa.Integer(), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("version_read", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(*['training_id', 'lesson_id', 'participant_email'], name="uq_training_ebook_progress"),
    )
    op.create_index("ix_training_ebook_progress_participant_email", "training_ebook_progress", ['participant_email'], unique=False)
    op.create_index("ix_training_ebook_progress_training_id", "training_ebook_progress", ['training_id'], unique=False)
    op.create_table(
        "training_ebook_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("ebook_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_training_ebook_versions_ebook_id", "training_ebook_versions", ['ebook_id'], unique=False)
    op.create_index("ix_training_ebook_versions_lookup", "training_ebook_versions", ['ebook_id', 'version'], unique=False)
    op.create_table(
        "training_ebooks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("training_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("section_id", sa.String(length=64), nullable=False),
        sa.Column("lesson_id", sa.String(length=64), nullable=False),
        sa.Column("draft", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("draft_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reviewer_note", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("submitted_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("published", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("published_version", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("published_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(*['training_id', 'lesson_id'], name="uq_training_ebook_lesson"),
    )
    op.create_index("ix_training_ebooks_status", "training_ebooks", ['status'], unique=False)
    op.create_index("ix_training_ebooks_tenant_id", "training_ebooks", ['tenant_id'], unique=False)
    op.create_index("ix_training_ebooks_training_id", "training_ebooks", ['training_id'], unique=False)


def downgrade():
    op.drop_table("training_ebooks")
    op.drop_table("training_ebook_versions")
    op.drop_table("training_ebook_progress")
    op.drop_table("training_ebook_notes")
    op.drop_table("training_ebook_bookmarks")
    op.drop_table("training_ebook_audit")
    op.drop_table("media_assets")
