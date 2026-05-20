from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from app.db.database import Base


class BlogCategory(Base):
    __tablename__ = "blog_categories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    name = Column(String(150), unique=True, nullable=False)
    slug = Column(String(150), unique=True, nullable=True)
    description = Column(Text, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    blogs = relationship("Blog", back_populates="category")


class Blog(Base):
    __tablename__ = "blogs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    merchant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category_id = Column(
        UUID(as_uuid=True),
        ForeignKey("blog_categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    title = Column(String(255), nullable=False)
    slug = Column(String(255), unique=True, nullable=True, index=True)
    short_description = Column(Text, nullable=True)
    content = Column(Text, nullable=False)
    featured_image = Column(Text, nullable=True)

    status = Column(String(50), default="DRAFT", nullable=False)
    approval_status = Column(String(50), default="PENDING", nullable=False)
    is_published = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    category = relationship("BlogCategory", back_populates="blogs")
    approval_logs = relationship(
        "BlogApprovalLog",
        back_populates="blog",
        cascade="all, delete-orphan",
    )


class BlogApprovalLog(Base):
    __tablename__ = "blog_approval_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    blog_id = Column(
        UUID(as_uuid=True),
        ForeignKey("blogs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    admin_id = Column(
        Integer,
        ForeignKey("admins.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    action = Column(String(50), nullable=True)
    remarks = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    blog = relationship("Blog", back_populates="approval_logs")
