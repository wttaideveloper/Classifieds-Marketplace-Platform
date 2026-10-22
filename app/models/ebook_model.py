"""eBook lessons: structured pages with a draft copy and a published copy, an approval trail, learner reading state, and the
image assets they use.

An eBook belongs to one lesson (type "ebook") of a training. The author edits `draft`; learners only ever read `published`, which is
a copy made when a Super Admin has approved the draft. Nothing about a page or an image is flattened into one text field.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.database import Base


class MediaAsset(Base):
    """An uploaded image with what the editor needs to know about it. The file itself is on the upload volume."""
    __tablename__ = "media_assets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    owner_user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    kind = Column(String(12), nullable=False, default="image")
    stored_name = Column(String(255), nullable=False, unique=True)         # the file under UPLOAD_DIR/training
    original_name = Column(String(255))
    content_type = Column(String(100), nullable=False)
    size = Column(Integer, nullable=False)
    width = Column(Integer)
    height = Column(Integer)
    sha256 = Column(String(64), index=True)
    scan_status = Column(String(12), nullable=False, default="skipped")    # clean | skipped (no scanner set up) | unscanned (scanner down)
    is_deleted = Column(Boolean, nullable=False, default=False)
    deleted_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class TrainingEbook(Base):
    __tablename__ = "training_ebooks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    training_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    section_id = Column(String(64), nullable=False)
    lesson_id = Column(String(64), nullable=False)

    draft = Column(JSONB, nullable=False)                    # {subtitle, author, description, cover_asset_id, completion_rule, allow_*, pages[]}
    draft_version = Column(Integer, nullable=False, default=1)
    # draft | pending_approval | changes_requested | approved. A change after approval puts it back to draft.
    status = Column(String(20), nullable=False, default="draft", index=True)
    reviewer_note = Column(Text)
    submitted_at = Column(DateTime)
    submitted_by = Column(UUID(as_uuid=True))
    reviewed_at = Column(DateTime)
    reviewed_by = Column(UUID(as_uuid=True))

    published = Column(JSONB)                                # the approved copy learners read; null = nothing live
    published_version = Column(Integer)                      # the draft_version it was copied from
    published_at = Column(DateTime)
    published_by = Column(UUID(as_uuid=True))

    created_by = Column(UUID(as_uuid=True))
    updated_by = Column(UUID(as_uuid=True))
    updated_by_name = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("training_id", "lesson_id", name="uq_training_ebook_lesson"),)


class TrainingEbookVersion(Base):
    """A saved copy of the draft at a moment that matters: when it was submitted, approved or published (or restored)."""
    __tablename__ = "training_ebook_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ebook_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    kind = Column(String(12), nullable=False)                # submitted | approved | published
    snapshot = Column(JSONB, nullable=False)
    created_by = Column(UUID(as_uuid=True))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_training_ebook_versions_lookup", "ebook_id", "version"),)


class TrainingEbookAudit(Base):
    """Every change to an eBook, with who made it. Rows are only ever added."""
    __tablename__ = "training_ebook_audit"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ebook_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    training_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    lesson_id = Column(String(64), nullable=False)
    action = Column(String(24), nullable=False)
    # created | saved | reordered | submitted | withdrawn | changes_requested | approved | published | unpublished | restored
    version = Column(Integer)
    from_status = Column(String(20))
    to_status = Column(String(20))
    note = Column(Text)
    actor_id = Column(String(64))
    actor_name = Column(String(255))
    actor_role = Column(String(20))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class TrainingEbookProgress(Base):
    __tablename__ = "training_ebook_progress"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    training_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    lesson_id = Column(String(64), nullable=False)
    participant_email = Column(String(255), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True))
    last_page_id = Column(String(64))
    last_page_number = Column(Integer)
    last_scroll_position = Column(Float)
    progress_percent = Column(Integer, nullable=False, default=0)           # the furthest the learner has read
    completed = Column(Boolean, nullable=False, default=False)
    completed_at = Column(DateTime)
    version_read = Column(Integer)                                          # the published version this was read in
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("training_id", "lesson_id", "participant_email", name="uq_training_ebook_progress"),)


class TrainingEbookBookmark(Base):
    __tablename__ = "training_ebook_bookmarks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    training_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    lesson_id = Column(String(64), nullable=False)
    participant_email = Column(String(255), nullable=False, index=True)
    page_id = Column(String(64), nullable=False)
    page_number = Column(Integer)
    label = Column(String(200))
    scroll_position = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_training_ebook_bookmarks_lookup", "training_id", "lesson_id", "participant_email"),)


class TrainingEbookNote(Base):
    __tablename__ = "training_ebook_notes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    training_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    lesson_id = Column(String(64), nullable=False)
    participant_email = Column(String(255), nullable=False, index=True)
    page_id = Column(String(64), nullable=False)
    page_number = Column(Integer)
    text = Column(Text, nullable=False)
    quote = Column(Text)                                                    # the passage the note is about
    color = Column(String(20))
    scroll_position = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_training_ebook_notes_lookup", "training_id", "lesson_id", "participant_email"),)
