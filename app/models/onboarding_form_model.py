import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.database import Base


class OnboardingForm(Base):
    __tablename__ = "onboarding_forms"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    entity_type = Column(String(50), nullable=False, index=True)
    status = Column(String(20), default="draft", nullable=False, index=True)
    published_at = Column(DateTime, nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    sections = relationship(
        "OnboardingFormSection",
        back_populates="form",
        cascade="all, delete-orphan",
        order_by="OnboardingFormSection.order",
    )

    __table_args__ = (
        Index("ix_onboarding_forms_entity_status", "entity_type", "status"),
    )


class OnboardingFormSection(Base):
    __tablename__ = "onboarding_form_sections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    form_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_forms.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title = Column(String(255), nullable=False)
    order = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    form = relationship("OnboardingForm", back_populates="sections")
    fields = relationship(
        "OnboardingFormField",
        back_populates="section",
        cascade="all, delete-orphan",
        order_by="OnboardingFormField.order",
    )

    __table_args__ = (
        UniqueConstraint("form_id", "order", name="uq_onboarding_form_sections_form_order"),
    )


class OnboardingFormField(Base):
    __tablename__ = "onboarding_form_fields"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    section_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_form_sections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label = Column(String(255), nullable=False)
    field_key = Column(String(100), nullable=False)
    field_type = Column(String(50), nullable=False)
    placeholder = Column(String(255))
    help_text = Column(Text)
    required = Column(Boolean, default=False, nullable=False)
    locked = Column(Boolean, default=False, nullable=False)
    visible = Column(Boolean, default=True, nullable=False)
    order = Column(Integer, nullable=False)
    options = Column(JSONB, default=list)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    section = relationship("OnboardingFormSection", back_populates="fields")

    __table_args__ = (
        UniqueConstraint("section_id", "order", name="uq_onboarding_form_fields_section_order"),
        UniqueConstraint("section_id", "field_key", name="uq_onboarding_form_fields_section_key"),
    )
