"""Tables behind group chat (see docs/group-chat-api.md).

The group itself is a row in `conversations` (conversation_type = "group") and its members are rows in
`conversation_participants`. These are the records that only exist for groups: the audit trail, what each person
agreed to, reports, reactions, the list of verified providers, messages saved to a clinical record, and patients'
requests about their data.

Nothing here is ever updated in place or deleted by the API, except where a row has a status that moves forward
(a report being reviewed, a consent being withdrawn, a verification being revoked).
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import deferred

from app.db.database import Base
from app.models.chat_model import PORTABLE_JSON


class GroupAuditLog(Base):
    """Read-only trail of what happened in a group: every admin action, role change, consent and message edit.
    The API only ever inserts and lists; there is no update or delete path."""

    __tablename__ = "group_audit_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    actor_id = Column(UUID(as_uuid=True), nullable=True)
    actor_role = Column(String(20), nullable=True)  # the platform role (admin, provider, customer, super_admin) or "system"
    action = Column(String(40), nullable=False, index=True)
    target_user_id = Column(UUID(as_uuid=True), nullable=True)
    message_id = Column(UUID(as_uuid=True), nullable=True)
    reason = Column(String(500), nullable=True)
    details = Column(PORTABLE_JSON, nullable=True)  # what changed (before / after); never message text of a health nature
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (Index("ix_group_audit_conversation_created", "conversation_id", "created_at"),)


class GroupConsent(Base):
    """What a person agreed to when they accepted an invitation, kept so they can see it later. For someone under 18
    the consent is the parent's or guardian's. Withdrawing sets withdrawn_at; the row stays."""

    __tablename__ = "group_consents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    granted_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    withdrawn_at = Column(DateTime, nullable=True)
    is_minor = Column(Boolean, default=False, nullable=False)
    guardian_name = Column(String(255), nullable=True)
    guardian_relationship = Column(String(80), nullable=True)
    notice = Column(PORTABLE_JSON, nullable=True)  # the invitation text they saw: purpose, members, what is visible


class GroupReport(Base):
    """A report of a message or a group, sent to the clinic's compliance team and the platform team."""

    __tablename__ = "group_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    message_id = Column(UUID(as_uuid=True), nullable=True)
    reporter_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    reason = Column(String(40), nullable=False)
    details = Column(String(1000), nullable=True)
    status = Column(String(12), default="open", nullable=False, index=True)  # open | reviewing | resolved | dismissed
    resolution_note = Column(String(1000), nullable=True)
    resolved_by = Column(UUID(as_uuid=True), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class MessageReaction(Base):
    __tablename__ = "group_message_reactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    conversation_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    emoji = Column(String(16), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("message_id", "user_id", "emoji", name="uq_group_reaction_message_user_emoji"),)


class VerifiedProvider(Base):
    """A provider whose registration has been checked. Only a verified provider can create or own a group, or be made
    an admin of one."""

    __tablename__ = "verified_providers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    registration_number = Column(String(80), nullable=True)
    registration_council = Column(String(120), nullable=True)
    status = Column(String(10), default="verified", nullable=False, index=True)  # verified | revoked
    verified_by = Column(UUID(as_uuid=True), nullable=True)
    verified_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    notes = Column(String(500), nullable=True)


class ClinicalRecordEntry(Base):
    """A message a provider saved to a patient's clinical record: a snapshot taken at the time (the message itself can
    later be hidden or edited), with who saved it and why."""

    __tablename__ = "group_clinical_record_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    message_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    patient_user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    saved_by = Column(UUID(as_uuid=True), nullable=False)
    sender_id = Column(UUID(as_uuid=True), nullable=False)
    content_snapshot = Column(Text, nullable=True)
    attachment_id = Column(UUID(as_uuid=True), nullable=True)
    message_created_at = Column(DateTime, nullable=False)
    note = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("message_id", "patient_user_id", name="uq_group_record_message_patient"),)


class GroupDataRequest(Base):
    """A patient asking for a copy of their data from group chat."""

    __tablename__ = "group_data_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    conversation_id = Column(UUID(as_uuid=True), nullable=True)  # null = all of their groups
    request_type = Column(String(20), default="copy", nullable=False)
    status = Column(String(12), default="received", nullable=False)  # received | in_progress | completed | failed
    note = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    processing_started_at = Column(DateTime, nullable=True)
    processing_token = Column(UUID(as_uuid=True), nullable=True)
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    error_code = Column(String(40), nullable=True)
    export_json = deferred(Column(Text, nullable=True))
    expires_at = Column(DateTime, nullable=True)

    __table_args__ = (Index("ix_group_data_requests_status_created", "status", "created_at"),)
