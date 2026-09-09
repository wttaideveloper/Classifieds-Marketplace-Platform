import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.database import Base


class ProgramFormConfiguration(Base):
    __tablename__ = "program_form_configurations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    scope = Column(String(20), nullable=False, default="global")  # global|selective
    status = Column(String(20), nullable=False, default="draft")  # draft|published|retired
    is_active = Column(Boolean, default=False, nullable=False)
    current_version = Column(Integer, default=1, nullable=False)
    created_by = Column(String(255))
    published_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    versions = relationship("ProgramFormConfigurationVersion", back_populates="configuration", cascade="all, delete-orphan")
    assignments = relationship("ProgramFormAssignment", back_populates="configuration", cascade="all, delete-orphan")
    audits = relationship("ProgramFormAudit", back_populates="configuration", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_program_form_config_scope_active", "scope", "is_active"),
    )


class ProgramFormConfigurationVersion(Base):
    __tablename__ = "program_form_configuration_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    configuration_id = Column(UUID(as_uuid=True), ForeignKey("program_form_configurations.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="draft")
    sections = Column(JSONB, default=list, nullable=False)
    created_by = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    published_at = Column(DateTime)

    configuration = relationship("ProgramFormConfiguration", back_populates="versions")

    __table_args__ = (
        UniqueConstraint("configuration_id", "version", name="uq_program_form_config_version"),
        Index("ix_program_form_version_status", "configuration_id", "status"),
    )


class ProgramFormAssignment(Base):
    __tablename__ = "program_form_assignments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    configuration_id = Column(UUID(as_uuid=True), ForeignKey("program_form_configurations.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    enterprise_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    created_by = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    configuration = relationship("ProgramFormConfiguration", back_populates="assignments")

    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_program_form_assignment_tenant"),
    )


class ProgramFormAudit(Base):
    __tablename__ = "program_form_audits"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    configuration_id = Column(UUID(as_uuid=True), ForeignKey("program_form_configurations.id", ondelete="CASCADE"), nullable=True, index=True)
    version_id = Column(UUID(as_uuid=True), ForeignKey("program_form_configuration_versions.id", ondelete="SET NULL"), nullable=True, index=True)
    actor_id = Column(String(255))
    action = Column(String(100), nullable=False)
    before = Column(JSONB)
    after = Column(JSONB)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    configuration = relationship("ProgramFormConfiguration", back_populates="audits")
