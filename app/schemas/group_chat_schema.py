"""Group chat request / response shapes. The contract is explained in docs/group-chat-api.md."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GroupKind = Literal["care", "program"]
GroupLinkType = Literal["program", "appointment_series", "care_episode", "training"]
GroupRole = Literal["owner", "admin", "member"]
MemberKind = Literal["provider", "patient"]
MembershipStatus = Literal["invited", "active", "declined", "removed", "left"]
ReportReason = Literal["harassment", "privacy", "health_information", "spam", "inappropriate", "safety", "other"]
ReportStatus = Literal["open", "reviewing", "resolved", "dismissed"]
GroupMessageType = Literal["text", "image", "document", "audio", "video"]


# ---------------------------------------------------------------- requests

def _check_avatar_url(value: str | None) -> str | None:
    if value is None or value == "":
        return value   # "" clears the picture on update
    if not value.startswith(("https://", "http://", "/api/v1/uploads/")):
        raise ValueError("avatar_url must be the url returned by POST /api/v1/uploads/ or an http(s) address")
    return value


class GroupInvitee(BaseModel):
    user_id: UUID
    display_name: str | None = Field(
        None, max_length=120,
        description="The name other patients will see in a program group. The invited person can change it when they accept.",
    )
    is_minor: bool = Field(False, description="Under 18: the invitation must be accepted with a parent's or guardian's consent.")


class GroupCreate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {
        "name": "Yoga batch Oct 2026", "kind": "program", "description": "Class updates and questions",
        "context_type": "program", "context_id": "550e8400-e29b-41d4-a716-446655440010",
        "invitees": [{"user_id": "550e8400-e29b-41d4-a716-446655440020"}],
    }})

    name: str = Field(..., min_length=1, max_length=120, description="Must not contain a patient's name or a health condition.")
    kind: GroupKind = Field(..., description="care = one patient's care team; program = several patients in one program.")
    description: str | None = Field(None, max_length=500)
    avatar_url: str | None = Field(
        None, max_length=500,
        description="The group icon: the `url` returned by POST /api/v1/uploads/ (for example /api/v1/uploads/group-avatars/ab12.png), "
                    "or an https:// address. Anything else is a 422.",
    )
    context_type: GroupLinkType = Field(..., description="Every group follows a program, an appointment series or a care episode.")
    context_id: UUID
    invitees: list[GroupInvitee] = Field(default_factory=list)
    admins_only_posting: bool | None = Field(None, description="Default: on for a program group, off for a care group.")
    invite_all_enrolled: bool = Field(
        False,
        description="Training groups only: also invite every eligible enrolled learner of the training, up to the group limit "
                    "(the people in `invitees` come first). The answer is the group; `GET /groups/{id}/members` shows who was invited.",
    )
    exclude_user_ids: list[UUID] = Field(default_factory=list, description="With invite_all_enrolled: people to leave out.")

    @model_validator(mode="after")
    def _training_rules(self):
        if self.context_type == "training" and self.kind != "program":
            raise ValueError("A training group is a program group: send kind = program")
        if self.invite_all_enrolled and self.context_type != "training":
            raise ValueError("invite_all_enrolled works for training groups only")
        return self

    _avatar = field_validator("avatar_url")(_check_avatar_url)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("A group needs a name")
        return value


class GroupUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=500)
    avatar_url: str | None = Field(None, max_length=500, description="The url returned by POST /api/v1/uploads/ (or an https:// address). Send an empty string to remove the picture.")

    _avatar = field_validator("avatar_url")(_check_avatar_url)


class GroupSettingsUpdate(BaseModel):
    admins_only_posting: bool = Field(..., description="true: only providers can post (announcements).")


class InviteMembers(BaseModel):
    invitees: list[GroupInvitee] = Field(..., min_length=1)


class InviteEnrolled(BaseModel):
    """Invite learners of a training group's training without sending thousands of ids."""
    user_ids: list[UUID] | None = Field(None, description="Invite exactly these enrolled learners (and verified providers).")
    all_eligible: bool = Field(False, description="Invite every eligible enrolled learner who is not already in or invited to this group, up to the free places.")
    exclude_user_ids: list[UUID] = Field(default_factory=list, description="With all_eligible: people to leave out.")

    @model_validator(mode="after")
    def _one_way(self):
        if self.all_eligible == bool(self.user_ids):
            raise ValueError("Send either user_ids or all_eligible = true")
        return self


class Guardian(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    relationship: str = Field(..., min_length=1, max_length=80, description="e.g. mother, father, legal guardian")


class AcceptInvitation(BaseModel):
    consent: bool = Field(..., description="Must be true. The person agrees to be added, having read the invitation.")
    display_name: str | None = Field(None, max_length=120, description="What other patients see in a program group.")
    guardian: Guardian | None = Field(None, description="Required when the invitation is for someone under 18.")


class DeclineInvitation(BaseModel):
    reason: str | None = Field(None, max_length=255)


class RoleChange(BaseModel):
    role: Literal["admin", "member"]
    reason: str | None = Field(None, max_length=255, description="Required when demoting an admin.")


class TransferOwnership(BaseModel):
    user_id: UUID = Field(..., description="An active member who is a verified provider.")


class ReassignOwner(BaseModel):
    user_id: UUID
    reason: str = Field(..., min_length=3, max_length=255)


class PostingMute(BaseModel):
    until: datetime | None = Field(None, description="When the mute ends (UTC). Omit for until an admin unmutes.")
    reason: str | None = Field(None, max_length=255)


class CloseGroup(BaseModel):
    reason: str | None = Field(None, max_length=255, description="e.g. the program ended.")


class ReopenGroup(BaseModel):
    reason: str = Field(..., min_length=3, max_length=255)


class GroupPreferences(BaseModel):
    muted: bool | None = Field(None, description="Mute notifications for this group (never removes access).")
    muted_until: datetime | None = None
    pinned: bool | None = Field(None, description="Pin the group in your own list.")


class SendGroupMessage(BaseModel):
    content: str | None = Field(None, max_length=4000)
    message_type: GroupMessageType = "text"
    attachment_id: UUID | None = None
    reply_to_id: UUID | None = Field(None, description="Quote / reply to a message of this group.")
    mentions: list[UUID] = Field(default_factory=list, description="Members tagged with @. They are notified directly.")
    mention_all: bool = Field(False, description="@everyone. Providers only.")


class EditGroupMessage(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)


class HideMessage(BaseModel):
    reason: str = Field(..., min_length=3, max_length=255)


class ReactionBody(BaseModel):
    emoji: str = Field(..., min_length=1, max_length=16)


class ReportCreate(BaseModel):
    message_id: UUID | None = Field(None, description="Omit to report the group itself.")
    reason: ReportReason
    details: str | None = Field(None, max_length=1000)


class ReportResolve(BaseModel):
    status: Literal["reviewing", "resolved", "dismissed"]
    note: str | None = Field(None, max_length=1000)


class LegalHold(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)


class ShareHistory(BaseModel):
    reason: str = Field(..., min_length=3, max_length=255, description="The clinical reason, recorded in the audit trail.")


class SaveToRecord(BaseModel):
    patient_user_id: UUID = Field(..., description="The patient whose clinical record the message is saved to.")
    note: str | None = Field(None, max_length=500)


class DataRequestCreate(BaseModel):
    conversation_id: UUID | None = Field(None, description="Omit for all of your groups.")
    note: str | None = Field(None, max_length=500)


class VerifiedProviderCreate(BaseModel):
    user_id: UUID
    registration_number: str | None = Field(None, max_length=80)
    registration_council: str | None = Field(None, max_length=120)
    tenant_id: UUID | None = None
    notes: str | None = Field(None, max_length=500)


# ---------------------------------------------------------------- responses

class EmergencyNotice(BaseModel):
    text: str
    number: str


class GroupLimits(BaseModel):
    max_members: int
    max_admins: int
    edit_window_minutes: int


class GroupPermissions(BaseModel):
    """What the caller can do with this group right now. Built by the server from the caller's role, the kind of member
    they are, whether the group is open and the group's posting rules, so the app does not have to work it out."""
    can_post: bool = Field(..., description="Send a message (false when closed, muted, or posting is for providers only and the caller is not one).")
    can_edit_group: bool = Field(..., description="Change the name, description and picture (PATCH /groups/{id}).")
    can_change_settings: bool = Field(..., description="Change who can send messages (PATCH /groups/{id}/settings).")
    can_invite: bool = Field(..., description="Invite people (POST /groups/{id}/members and /members/invite-enrolled).")
    can_view_members: bool
    can_view_pending_invitations: bool = Field(..., description="The members list also returns people who were invited and have not answered.")
    can_remove_members: bool
    can_make_admin: bool
    can_remove_admin: bool = Field(..., description="Take admin away from someone (owner or compliance; a reason is required).")
    can_mute_members: bool = Field(..., description="Stop a member posting (POST .../mute-posting).")
    can_hide_messages: bool = Field(..., description="Remove any message from view, with a reason.")
    can_pin_messages: bool
    can_close: bool
    can_reopen: bool = Field(..., description="The group is closed and the caller is its owner or compliance.")
    can_transfer_ownership: bool
    can_reassign_owner: bool = Field(..., description="Compliance only.")
    can_leave: bool = Field(..., description="Any active member except the owner (who hands the group over first).")
    can_view_audit: bool = Field(..., description="The owner and compliance.")
    can_set_legal_hold: bool = Field(..., description="Compliance only.")
    can_report: bool
    can_mute_group: bool = Field(..., description="Mute notifications or pin the group for myself (PATCH /groups/{id}/preferences).")


class GroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    kind: GroupKind
    description: str | None = None
    avatar_url: str | None = None
    context_type: str | None = None
    context_id: UUID | None = None
    context_name: str | None = Field(None, description="Training groups: the title of the linked training.")
    tenant_id: UUID | None = None
    status: Literal["open", "closed"]
    admins_only_posting: bool
    created_by: UUID
    created_at: datetime
    member_count: int = Field(..., description="Active members: the people who can see this group.")
    visibility_notice: str = Field(..., description='"N people can see this" - show it on every group.')
    emergency_notice: EmergencyNotice
    forwarding_blocked: bool = True
    screenshots_blocked: bool = Field(True, description="A hint to the app: block screenshots where the phone allows it.")
    limits: GroupLimits
    my_role: GroupRole | None = None
    my_kind: MemberKind | None = None
    permissions: GroupPermissions | None = Field(None, description="What the caller can do with this group. Use it instead of working permissions out from my_role.")
    muted: bool = False
    pinned: bool = False
    is_archived: bool = False
    unread_count: int = 0
    last_message_at: datetime | None = None
    last_message_preview: str | None = None
    closed_at: datetime | None = None
    closed_reason: str | None = None
    retention_started_at: datetime | None = None
    retention_until: datetime | None = None
    legal_hold: bool = False


class GroupListResponse(BaseModel):
    items: list[GroupResponse]
    total: int


class MemberResponse(BaseModel):
    user_id: UUID
    name: str | None = Field(None, description="Full name for providers; a display name for patients where privacy applies.")
    role: GroupRole | None = None
    kind: MemberKind | None = None
    status: MembershipStatus
    is_me: bool = False
    joined_at: datetime | None = None
    posting_muted: bool = False
    posting_muted_until: datetime | None = None


class MembersResponse(BaseModel):
    members: list[MemberResponse]
    total: int


class InvitationResponse(BaseModel):
    """What a person is shown before they agree to be added. They can read nothing in the group until they accept."""
    conversation_id: UUID
    name: str
    kind: GroupKind
    description: str | None = None
    purpose: str
    invited_by_name: str | None = None
    provider_names: list[str] = Field(default_factory=list)
    member_count: int
    visibility_notice: str
    what_members_can_see: str
    emergency_notice: EmergencyNotice
    requires_guardian_consent: bool = False
    invited_at: datetime | None = None


class InvitationsResponse(BaseModel):
    items: list[InvitationResponse]
    total: int


class QuotedMessage(BaseModel):
    id: UUID
    sender_name: str | None = None
    preview: str | None = None
    is_hidden: bool = False


class GroupMessageResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    sender_id: UUID
    sender_name: str | None = None
    sender_kind: MemberKind | None = None
    message_type: str
    content: str | None = None
    attachment_id: UUID | None = None
    status: Literal["visible", "retracted", "removed_by_admin"] = "visible"
    placeholder: str | None = Field(None, description='"Message retracted" / "Message removed by admin" when hidden.')
    reply_to: QuotedMessage | None = None
    mentions: list[UUID] = Field(default_factory=list)
    mentions_all: bool = False
    mentions_me: bool = False
    is_edited: bool = False
    edited_at: datetime | None = None
    pinned: bool = False
    reactions: dict[str, int] = Field(default_factory=dict)
    my_reactions: list[str] = Field(default_factory=list)
    read_by: list[UUID] | None = Field(None, description="Care groups only. null in a program group.")
    created_at: datetime


class GroupMessagesResponse(BaseModel):
    items: list[GroupMessageResponse]
    has_more: bool
    next_cursor: str | None = None
    unread_count: int = 0
    first_unread_message_id: UUID | None = Field(None, description='Put the "new messages" marker above this message.')
    pinned_count: int = 0


class ReadStatusResponse(BaseModel):
    message_id: UUID
    seen_by_count: int
    recipient_count: int
    label: str = Field(..., description='e.g. "seen by 4 of 6"')
    seen_by: list[UUID] | None = Field(None, description="Care groups only.")


class AuditEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    actor_id: UUID | None = None
    actor_role: str | None = None
    action: str
    target_user_id: UUID | None = None
    message_id: UUID | None = None
    reason: str | None = None
    details: dict | None = None
    created_at: datetime


class AuditResponse(BaseModel):
    items: list[AuditEntryResponse]
    total: int


class ReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    message_id: UUID | None = None
    reporter_id: UUID
    reason: str
    details: str | None = None
    status: ReportStatus
    resolution_note: str | None = None
    resolved_by: UUID | None = None
    resolved_at: datetime | None = None
    created_at: datetime


class ReportsResponse(BaseModel):
    items: list[ReportResponse]
    total: int


class ConsentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    conversation_id: UUID
    group_name: str | None = None
    granted_at: datetime
    withdrawn_at: datetime | None = None
    is_minor: bool = False
    guardian_name: str | None = None
    guardian_relationship: str | None = None
    notice: dict | None = None


class ConsentsResponse(BaseModel):
    items: list[ConsentResponse]


class DataRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID | None = None
    request_type: str
    status: str
    note: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class GrievanceOfficerResponse(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    configured: bool


class VerifiedProviderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    name: str | None = Field(None, description="The provider's display name, so the list can be read without a second lookup. null when no name is known.")
    tenant_id: UUID | None = None
    registration_number: str | None = None
    registration_council: str | None = None
    status: Literal["verified", "revoked"]
    verified_by: UUID | None = None
    verified_at: datetime
    revoked_at: datetime | None = None
    notes: str | None = None


class ClinicalRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    message_id: UUID
    patient_user_id: UUID
    saved_by: UUID
    sender_id: UUID
    content_snapshot: str | None = None
    message_created_at: datetime
    note: str | None = None
    created_at: datetime


class OriginalMessageResponse(BaseModel):
    message_id: UUID
    sender_id: UUID
    status: str
    content: str | None = Field(None, description="The text as first written.")
    versions: list[dict] = Field(default_factory=list, description="Earlier versions, oldest first.")
    current_content: str | None = Field(None, description="The text now (after any edits), even when the message is hidden.")
    hidden_by: UUID | None = None
    hidden_reason: str | None = None
    hidden_at: datetime | None = None
    created_at: datetime


# ---------------------------------------------------------------- training groups

class TrainingForGroupResponse(BaseModel):
    id: UUID
    title: str
    status: str | None = None
    delivery_mode: str | None = None
    instructor_name: str | None = Field(None, description="The text typed in the training form. Information only: it is never turned into a member.")
    instructor_user_id: UUID | None = Field(None, description="Training.instructor_id when the training has one (the assigned Provider).")
    instructor_is_verified_provider: bool = False
    enrolled_count: int = Field(0, description="Enrolled learners who can be invited (enrolled, active, completed, approved or attended).")
    group_count: int = Field(0, description="Open and closed groups already linked to this training.")
    created_at: datetime | None = None


class TrainingsForGroupsResponse(BaseModel):
    items: list[TrainingForGroupResponse]
    total: int
    page: int
    page_size: int


class TrainingLearnerResponse(BaseModel):
    enrolment_id: UUID
    name: str | None = None
    email: str | None = Field(None, description="For the clinic's staff, as in the enrolment list.")
    enrolment_status: str
    user_id: UUID | None = Field(None, description="The learner's application user id: the id to invite. null = no chat account found.")
    can_invite: bool
    reason: str | None = Field(None, description="Why can_invite is false: not_eligible_status | no_account | already_member | already_invited.")
    group_state: Literal["none", "invited", "member", "declined", "left", "removed"] = "none"
    group_ids: list[UUID] = Field(default_factory=list, description="Groups of this training the learner is invited to or a member of.")
    enrolled_at: datetime | None = None


class TrainingLearnersResponse(BaseModel):
    training_id: UUID
    training_title: str
    items: list[TrainingLearnerResponse]
    total: int
    page: int
    page_size: int
    eligible_total: int = Field(..., description="Eligible enrolments of the training, whatever the filters.")
    max_members: int = Field(..., description="The group limit, owner and pending invitations included.")


class TrainingProviderResponse(BaseModel):
    user_id: UUID
    name: str | None = None
    registration_number: str | None = None
    is_training_instructor: bool = False
    is_me: bool = False


class TrainingProvidersResponse(BaseModel):
    training_id: UUID
    instructor_name: str | None = None
    items: list[TrainingProviderResponse]
    total: int


class InviteEnrolledResponse(BaseModel):
    invited: list[UUID]
    skipped: list[UUID] = Field(default_factory=list, description="Already active, already invited, or the caller.")
    not_invited_group_full: int = Field(0, description="Eligible learners left out because the group reached its limit. Make another group for them.")
    no_account: int = Field(0, description="Eligible enrolments with no chat account (no user id could be found).")
    free_places: int = Field(..., description="Places left in the group after these invitations.")


class ProviderCandidateResponse(BaseModel):
    user_id: UUID
    name: str | None = None
    roles: list[str] = Field(default_factory=list, description="The member's roles in the clinic, as the identity service reports them.")
    verification: Literal["not_verified", "verified", "revoked"]
    registration_number: str | None = Field(None, description="Only for a verified provider.")


class ProviderCandidatesResponse(BaseModel):
    tenant_id: UUID
    items: list[ProviderCandidateResponse]
    total: int
    page: int
    page_size: int
