import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from app.db.database import Base


class EventMedia(Base):
    """A file uploaded for an Event form field (primary image, gallery, videos, documents).

    The Event itself keeps storing plain hosted URLs; this row is the ownership / validation /
    lifecycle record behind such a URL. A row is *pending* (attached_at NULL) until an Event create
    or update references its URL; unreferenced rows are deleted after a grace period.
    """

    __tablename__ = "event_media"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    enterprise_id = Column(UUID(as_uuid=True), nullable=True)
    uploaded_by = Column(String(64), nullable=True)  # application user id of the uploader
    field = Column(String(20), nullable=False)  # primary_image|gallery_images|videos|documents
    original_name = Column(String(255), nullable=False)
    ext = Column(String(10), nullable=False)
    mime_type = Column(String(100), nullable=False)
    size = Column(Integer, nullable=False)
    client_ref = Column(String(64), nullable=True)  # caller-chosen key that makes upload retries idempotent
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    attached_at = Column(DateTime, nullable=True)  # first time an Event referenced it
    released_at = Column(DateTime, nullable=True)  # last time an Event stopped referencing it

    __table_args__ = (
        Index("ix_event_media_uploader_client_ref", "uploaded_by", "client_ref"),
    )
