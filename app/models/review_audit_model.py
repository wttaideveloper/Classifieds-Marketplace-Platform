import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, String
from sqlalchemy.dialects.postgresql import UUID

from app.db.database import Base


class ReviewModerationLog(Base):
    """One row each time a moderator changes a review's status or removes a review (any module)."""

    __tablename__ = "review_moderation_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    module = Column(String(20), nullable=False)  # training | product | service | event
    review_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    item_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    item_name = Column(String(255), nullable=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)  # the business that owns the item
    from_status = Column(String(20), nullable=True)
    to_status = Column(String(20), nullable=False)  # approved | rejected | pending | deleted
    actor_user_id = Column(UUID(as_uuid=True), nullable=True)
    actor_role = Column(String(30), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_review_moderation_log_module_created", "module", "created_at"),)
