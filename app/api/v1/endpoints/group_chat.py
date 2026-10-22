"""Group chat API (mounted at /api/v1/groups). Contract and rules: docs/group-chat-api.md.

Fixed paths (invitations, consents, reports, verified providers, compliance) come before the /{group_id} ones so they
are never read as a group id.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.dependencies import extract_access_token, get_current_user
from app.db.database import get_db
from app.schemas.group_chat_schema import (
    InviteEnrolled, InviteEnrolledResponse, TrainingLearnersResponse, TrainingProvidersResponse, TrainingsForGroupsResponse,
    AcceptInvitation, AuditResponse, CloseGroup, ClinicalRecordResponse, ConsentsResponse, DataRequestCreate,
    DataRequestResponse, DeclineInvitation, EditGroupMessage, GrievanceOfficerResponse, GroupCreate, GroupListResponse,
    GroupMessageResponse, GroupMessagesResponse, GroupPreferences, GroupResponse, GroupSettingsUpdate, GroupUpdate,
    HideMessage, InvitationResponse, InvitationsResponse, InviteMembers, LegalHold, MembersResponse, OriginalMessageResponse,
    PostingMute, ReactionBody, ReadStatusResponse, ReassignOwner, ReopenGroup, ReportCreate, ReportResolve, ReportResponse,
    ReportsResponse, RoleChange, SaveToRecord, SendGroupMessage, ShareHistory, TransferOwnership, VerifiedProviderCreate,
    VerifiedProviderResponse, ProviderCandidatesResponse,
)
from app.services import group_chat_compliance as compliance
from app.services import group_chat_messages as messages
from app.services import group_chat_service as service
from app.services import group_chat_training as training_service

router = APIRouter(tags=["Group Chat"])


def token(request: Request) -> str | None:
    return extract_access_token(request)


# =============================================================================================================
# fixed paths first
# =============================================================================================================

@router.post(
    "", response_model=GroupResponse, status_code=status.HTTP_201_CREATED, summary="Create a group",
    description=(
        "Verified providers and clinic admins only; patients get 403. Every group is linked to a program, an appointment "
        "series or a care episode (`context_type` + `context_id`). The creator is the owner. The name must not contain a "
        "patient's name or a health condition (422). Each invitee gets an invitation and is **not** a member, and sees "
        "nothing, until they accept."
    ),
)
def create_group(request: Request, payload: GroupCreate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return service.create_group(db, current_user, payload, token(request))


@router.get(
    "", response_model=GroupListResponse, summary="My groups (the Groups tab)",
    description="Groups the caller is an active member of. `archived=true` lists the ones they archived (use `PATCH /conversations/{id}/archive`).",
)
def list_groups(
    kind: str | None = Query(None, pattern="^(care|program)$"), archived: bool = False, q: str | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db), current_user: dict = Depends(get_current_user),
):
    return service.list_groups(db, current_user, kind=kind, archived=archived, search=q, page=page, page_size=page_size)


# ---- training groups: pick a training, pick the people, create the group (docs/group-chat-training.md)

@router.get(
    "/trainings", response_model=TrainingsForGroupsResponse, summary="Trainings I can start a group for",
    description=(
        "Step 1 of creating a training group. Enterprise Admin: every training of the clinic. Provider: the trainings they are "
        "assigned to (`instructor_id`), or all of the clinic's when GROUP_PROVIDER_REQUIRES_TRAINING_ASSIGNMENT is off. Verified "
        "Providers and Enterprise Admins only. Each item says how many learners can be invited and how many groups it already has."
    ),
)
def group_trainings(
    request: Request, q: str | None = Query(None, max_length=200), status_filter: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db), current_user: dict = Depends(get_current_user),
):
    return training_service.manageable_trainings(db, current_user, token(request), q=q, status=status_filter, page=page, page_size=page_size)


@router.get(
    "/trainings/{training_id}/learners", response_model=TrainingLearnersResponse, summary="Enrolled learners of a training (searchable, paged)",
    description=(
        "Step 2. Each row has the `user_id` to invite (null = no chat account found, `can_invite` false), the enrolment status and "
        "`group_state` (none | invited | member | declined | left | removed). `q` searches name and email. By default only learners "
        "who can be invited are listed; `eligible_only=false` or `enrolment_status` shows the others. `not_in_group=true` hides "
        "everyone already in or invited to a group of this training (or to `group_id`) - use it to find people who enrolled later. "
        "403 for a training of another clinic, or for a Provider not assigned to it. A learner can be in several groups of one training: "
        "without `group_id` the list is for a NEW group, so `can_invite` ignores other groups (`group_state` and `group_ids` still say where "
        "the learner is); with `group_id`, `can_invite` is about that group (`already_member` / `already_invited`)."
    ),
)
def group_training_learners(
    request: Request, training_id: UUID, q: str | None = Query(None, max_length=200),
    enrolment_status: str | None = Query(None), eligible_only: bool = True, not_in_group: bool = False, group_id: UUID | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db), current_user: dict = Depends(get_current_user),
):
    return training_service.list_learners(
        db, current_user, training_id, token(request), q=q, enrolment_status=enrolment_status, eligible_only=eligible_only,
        not_in_group=not_in_group, group_id=group_id, page=page, page_size=page_size,
    )


@router.get(
    "/trainings/{training_id}/providers", response_model=TrainingProvidersResponse, summary="Verified Providers I can add to a training group",
    description=(
        "Step 3. The clinic's verified Providers. `is_training_instructor` marks the Provider the training is assigned to "
        "(`instructor_id`); the instructor name typed in the training form is returned as text only and nobody is added from it."
    ),
)
def group_training_providers(
    request: Request, training_id: UUID, q: str | None = Query(None, max_length=200),
    db: Session = Depends(get_db), current_user: dict = Depends(get_current_user),
):
    return training_service.list_providers(db, current_user, training_id, token(request), q=q)


@router.get(
    "/trainings/{training_id}/groups", response_model=GroupListResponse, summary="Groups already made for a training",
    description="Groups of this training the caller is in; the clinic's Enterprise Admin sees all of them (details only, never messages).",
)
def group_training_groups(request: Request, training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return training_service.training_groups(db, current_user, training_id, token(request))


@router.get("/invitations", response_model=InvitationsResponse, summary="My pending group invitations")
def list_invitations(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return service.list_invitations(db, current_user)


@router.get(
    "/invitations/{group_id}", response_model=InvitationResponse,
    summary="One invitation, as the person is shown it before agreeing",
    description="What the group is for, who the providers are, how many people can see it, and what members can see. Nothing from inside the group.",
)
def get_invitation(group_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return service.get_invitation(db, current_user, group_id)


@router.get("/consents/me", response_model=ConsentsResponse, summary="What I agreed to",
            description="Every consent the caller has given, with the invitation text they saw, and when it was withdrawn if it was.")
def my_consents(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return compliance.list_my_consents(db, current_user)


@router.post("/data-requests", response_model=DataRequestResponse, status_code=status.HTTP_201_CREATED,
             summary="Ask for a copy of my group chat data")
def create_data_request(payload: DataRequestCreate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return compliance.create_data_request(db, current_user, payload)


@router.get("/data-requests/me", response_model=list[DataRequestResponse], summary="My data requests")
def my_data_requests(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return compliance.list_my_data_requests(db, current_user)


@router.get("/data-requests/{request_id}/download", summary="Download my completed group data export")
def download_data_request(request_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.services import group_data_export
    data = group_data_export.download(db, request_id, current_user)
    return Response(content=data, media_type="application/json", headers={"Content-Disposition": f'attachment; filename="group-data-{request_id}.json"',
                                                                          "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/data-requests/{request_id}/retry", response_model=DataRequestResponse, summary="Retry my failed data export")
def retry_data_request(request_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.services import group_data_export
    return group_data_export.retry(db, request_id, current_user)


@router.get("/grievance-officer", response_model=GrievanceOfficerResponse, summary="Who to contact with a grievance",
            description="`configured` is false until the clinic sets the officer's details.")
def get_grievance_officer(current_user: dict = Depends(get_current_user)):
    return compliance.grievance_officer()


@router.post("/verified-providers", response_model=VerifiedProviderResponse, status_code=status.HTTP_201_CREATED,
             summary="Record that a provider's registration has been verified",
             description="Clinic admin (own clinic) or Super Admin. The person must be an active member of the clinic with a provider, admin or owner role (422 otherwise). Only a verified provider can create or own a group or be made an admin.")
def verify_provider(request: Request, payload: VerifiedProviderCreate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return compliance.verify_provider(db, current_user, payload, token(request))


@router.get("/verified-providers", response_model=list[VerifiedProviderResponse], summary="Verified providers")
def list_verified_providers(request: Request, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return compliance.list_verified_providers(db, current_user, token(request))


@router.get(
    "/verified-providers/candidates", response_model=ProviderCandidatesResponse, summary="Clinic members that can be verified (the picker)",
    description=(
        "For the \"Add provider\" screen. Clinic admin: the members of their own clinic; Super Admin: pass `tenant_id`. By default "
        "only active members with a provider-type role who are not verified now (someone whose verification was revoked is listed as `revoked`, so they can be verified again). `all_roles=true` lists every active member; "
        "`include_verified=true` also lists the people who are verified now (`verification` says which). `q` searches the name and role."
    ),
)
def provider_candidates(
    request: Request, tenant_id: UUID | None = None, q: str | None = Query(None, max_length=200), include_verified: bool = False,
    all_roles: bool = False, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db), current_user: dict = Depends(get_current_user),
):
    return compliance.list_provider_candidates(
        db, current_user, token(request), tenant_id=tenant_id, q=q, include_verified=include_verified, all_roles=all_roles,
        page=page, page_size=page_size,
    )


@router.delete("/verified-providers/{user_id}", response_model=VerifiedProviderResponse, summary="Revoke a provider's verification")
def revoke_provider(request: Request, user_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return compliance.revoke_provider(db, current_user, user_id, token(request))


@router.get("/reports", response_model=ReportsResponse, summary="Reports sent to compliance",
            description="Clinic admin sees the clinic's reports; Super Admin sees all.")
def list_reports(
    request: Request, status_filter: str | None = Query(None, alias="status", pattern="^(open|reviewing|resolved|dismissed)$"),
    group_id: UUID | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db), current_user: dict = Depends(get_current_user),
):
    return compliance.list_reports(db, current_user, status=status_filter, conversation_id=group_id, page=page, page_size=page_size,
                                   access_token=token(request))


@router.patch("/reports/{report_id}", response_model=ReportResponse, summary="Review or resolve a report")
def resolve_report(request: Request, report_id: UUID, payload: ReportResolve, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return compliance.resolve_report(db, current_user, report_id, payload, token(request))


@router.get("/compliance/groups", response_model=GroupListResponse, summary="All groups, for compliance",
            description="Clinic admin: the clinic's groups. Super Admin: all groups. Shows details, not messages.")
def compliance_groups(
    request: Request, kind: str | None = Query(None, pattern="^(care|program)$"),
    status_filter: str | None = Query(None, alias="status", pattern="^(open|closed)$"), legal_hold: bool | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db), current_user: dict = Depends(get_current_user),
):
    return compliance.list_groups_for_compliance(db, current_user, kind=kind, status=status_filter, legal_hold=legal_hold,
                                                 page=page, page_size=page_size, access_token=token(request))


# =============================================================================================================
# one group
# =============================================================================================================

@router.get("/{group_id}", response_model=GroupResponse, summary="Group details",
            description="Includes `visibility_notice` (\"N people can see this\") and the \"Not for emergencies\" notice to show on every group screen.")
def get_group(request: Request, group_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return service.get_group(db, current_user, group_id, token(request))


@router.patch("/{group_id}", response_model=GroupResponse, summary="Change the name, description or picture",
              description="Owner or admin. Each change is written to the audit trail.")
def update_group(request: Request, group_id: UUID, payload: GroupUpdate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return service.update_group(db, current_user, group_id, payload, token(request))


@router.patch("/{group_id}/settings", response_model=GroupResponse, summary="Posting by providers only (announcements)",
              description="Owner or admin. On by default for a program group, off for a care group.")
def update_settings(group_id: UUID, payload: GroupSettingsUpdate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return service.update_settings(db, current_user, group_id, payload)


@router.patch("/{group_id}/preferences", response_model=GroupResponse, summary="Mute / pin this group for me",
              description="Muting never removes access. For archiving use `PATCH /conversations/{id}/archive`.")
def update_preferences(group_id: UUID, payload: GroupPreferences, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return service.update_preferences(db, current_user, group_id, payload)


# ---- invitation and consent

@router.post("/{group_id}/accept", response_model=GroupResponse, summary="Accept an invitation (gives consent)",
             description="`consent` must be true. Someone under 18 must also send `guardian`. Messages are visible only from this moment on.")
def accept_invitation(group_id: UUID, payload: AcceptInvitation, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return service.accept_invitation(db, current_user, group_id, payload)


@router.post("/{group_id}/decline", summary="Decline an invitation")
def decline_invitation(group_id: UUID, payload: DeclineInvitation | None = None, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return service.decline_invitation(db, current_user, group_id, payload)


@router.post("/{group_id}/consent/withdraw", summary="Withdraw my consent",
             description="Takes effect at once: the person leaves the group and loses access. The consent record is kept with the withdrawal time.")
def withdraw_consent(group_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return service.withdraw_consent(db, current_user, group_id)


@router.post("/{group_id}/leave", summary="Leave the group",
             description="The owner must hand the group over first (409).")
def leave_group(group_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return service.leave_group(db, current_user, group_id)


# ---- members

@router.get("/{group_id}/members", response_model=MembersResponse, summary="Who is in the group",
            description="Providers are named in full. In a program group a patient sees other patients by display name only. Admins also see pending invitations.")
def list_members(group_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return service.list_members(db, current_user, group_id)


@router.post("/{group_id}/members", status_code=status.HTTP_201_CREATED, summary="Invite people",
             description="Owner or admin. By named invitation only (there are no invite links). Each person must accept.")
def invite_members(request: Request, group_id: UUID, payload: InviteMembers, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return service.invite_members(db, current_user, group_id, payload, token(request))


@router.post(
    "/{group_id}/members/invite-enrolled", response_model=InviteEnrolledResponse, status_code=status.HTTP_201_CREATED,
    summary="Training groups: invite enrolled learners without sending every id",
    description=(
        "Owner or admin of a group linked to a training. Send `user_ids` (they must be enrolled in the training, or verified "
        "Providers of the clinic; otherwise 422 with `invalid_user_ids`) or `all_eligible: true` (everyone eligible who is not in "
        "or invited to this group, oldest enrolment first, up to the free places). `not_invited_group_full` counts the learners "
        "left out because the group is full - make another group for them. Use it again later to invite people who enrolled afterwards."
    ),
)
def invite_enrolled(request: Request, group_id: UUID, payload: InviteEnrolled, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return service.invite_enrolled(db, current_user, group_id, payload, token(request))


@router.delete("/{group_id}/members/{user_id}", summary="Remove a member (or cancel an invitation)",
               description="Owner or admin. The person is told and loses access straight away. The owner cannot be removed. Removing an admin needs the owner or compliance, and a `reason`.")
def remove_member(
    request: Request, group_id: UUID, user_id: UUID, reason: str | None = Query(None, max_length=255),
    db: Session = Depends(get_db), current_user: dict = Depends(get_current_user),
):
    return service.remove_member(db, current_user, group_id, user_id, reason, token(request))


@router.patch("/{group_id}/members/{user_id}/role", summary="Make a member an admin, or an admin a member",
              description="Making someone an admin: owner or admin, and only a verified provider (patients cannot be admins), at most 3 admins. Demoting an admin: the owner or compliance, with a `reason`.")
def change_role(request: Request, group_id: UUID, user_id: UUID, payload: RoleChange, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return service.change_role(db, current_user, group_id, user_id, payload, token(request))


@router.post("/{group_id}/members/{user_id}/mute-posting", summary="Mute someone (they can read but not post)",
             description="Owner or admin. Optional `until`. The owner and other admins cannot be muted by an admin.")
def mute_posting(group_id: UUID, user_id: UUID, payload: PostingMute, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return service.mute_posting(db, current_user, group_id, user_id, payload)


@router.delete("/{group_id}/members/{user_id}/mute-posting", summary="Unmute someone")
def unmute_posting(group_id: UUID, user_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return service.unmute_posting(db, current_user, group_id, user_id)


@router.post("/{group_id}/members/{user_id}/share-history", summary="Share earlier messages with a care-team member",
             description="Care groups only, owner only, a recorded reason, and only where the patient has agreed (`POST .../history-sharing/consent`). Never in a program group.")
def share_history(group_id: UUID, user_id: UUID, payload: ShareHistory, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return compliance.share_history(db, current_user, group_id, user_id, payload)


@router.post("/{group_id}/history-sharing/consent", summary="Patient: agree that earlier history may be shared with the care team")
def agree_history_sharing(group_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return compliance.set_history_sharing_consent(db, current_user, group_id, True)


@router.delete("/{group_id}/history-sharing/consent", summary="Patient: stop agreeing to share earlier history")
def withdraw_history_sharing(group_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return compliance.set_history_sharing_consent(db, current_user, group_id, False)


# ---- ownership, closing, legal hold

@router.post("/{group_id}/transfer-ownership", summary="Hand the group to another verified provider", description="Owner only.")
def transfer_ownership(group_id: UUID, payload: TransferOwnership, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return service.transfer_ownership(db, current_user, group_id, payload)


@router.post("/{group_id}/reassign-owner", summary="Compliance names a new owner",
             description="The clinic's compliance admin or the platform, with a recorded reason (for example the owner cannot be reached).")
def reassign_owner(request: Request, group_id: UUID, payload: ReassignOwner, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return service.reassign_owner(db, current_user, group_id, payload, token(request))


@router.post("/{group_id}/close", response_model=GroupResponse, summary="Close the group (program or care episode ended)",
             description="Owner or admin. The group becomes read-only and the retention period starts.")
def close_group(group_id: UUID, payload: CloseGroup | None = None, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return service.close_group(db, current_user, group_id, payload)


@router.post("/{group_id}/reopen", response_model=GroupResponse, summary="Reopen a closed group",
             description="The owner (or compliance), with a reason.")
def reopen_group(request: Request, group_id: UUID, payload: ReopenGroup, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return service.reopen_group(db, current_user, group_id, payload, token(request))


@router.post("/{group_id}/legal-hold", summary="Put the group on legal hold", description="Compliance only. The record is then kept whatever the retention period says.")
def set_legal_hold(request: Request, group_id: UUID, payload: LegalHold, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return compliance.set_legal_hold(db, current_user, group_id, payload, token(request))


@router.delete("/{group_id}/legal-hold", summary="Lift the legal hold")
def clear_legal_hold(request: Request, group_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return compliance.clear_legal_hold(db, current_user, group_id, token(request))


# ---- audit and reports

@router.get("/{group_id}/audit", response_model=AuditResponse, summary="Audit trail (read only)",
            description="Admin actions, role changes, consents and message edits, newest first. The group owner and compliance can read it; nobody can change it.")
def audit_trail(
    request: Request, group_id: UUID, action: str | None = None, target_user_id: UUID | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db), current_user: dict = Depends(get_current_user),
):
    return compliance.list_audit(db, current_user, group_id, action=action, target_user_id=target_user_id, page=page,
                                 page_size=page_size, access_token=token(request))


@router.post("/{group_id}/reports", response_model=ReportResponse, status_code=status.HTTP_201_CREATED,
             summary="Report a message or the group",
             description="Any member. Goes to the clinic's compliance team and the platform team.")
def report(request: Request, group_id: UUID, payload: ReportCreate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return compliance.create_report(db, current_user, group_id, payload, token(request))


# ---- messages

@router.get("/{group_id}/messages", response_model=GroupMessagesResponse, summary="Read the messages",
            description=(
                "Newest page first by `cursor`; items are returned oldest to newest. Only messages sent after the caller joined. "
                "A retracted message shows `placeholder: \"Message retracted\"`, one an admin removed shows \"Message removed by admin\", "
                "with no content. `first_unread_message_id` is where to put the \"new messages\" marker. Mark the group read with `POST /groups/{id}/read`."
            ))
def list_messages(group_id: UUID, cursor: str | None = None, limit: int = Query(50, ge=1, le=100), db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return messages.list_messages(db, current_user, group_id, cursor=cursor, limit=limit)


@router.post("/{group_id}/messages", response_model=GroupMessageResponse, status_code=status.HTTP_201_CREATED, summary="Send a message",
             description=(
                 "Text, image, document, audio or video (upload with the attachment API first and pass `attachment_id`). "
                 "`reply_to_id` quotes a message, `mentions` tags members (they are notified directly, without the text), "
                 "`mention_all` tags everyone (providers only). 403 when an admin muted the sender, or posting is providers-only."
             ))
def send_message(group_id: UUID, payload: SendGroupMessage, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return messages.send_message(db, current_user, group_id, payload)


@router.post("/{group_id}/read", summary="Mark the group as read")
def mark_read(group_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.services.chat_service import mark_conversation_read_service

    messages_ok = service.common.load_group(db, group_id)  # 404 for anything that is not a group
    return mark_conversation_read_service(db, current_user, messages_ok.id)


@router.get("/{group_id}/pinned", response_model=GroupMessagesResponse, summary="Pinned messages")
def list_pinned(group_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return messages.list_pinned(db, current_user, group_id)


@router.get("/{group_id}/search", response_model=GroupMessagesResponse, summary="Search this group",
            description="Only messages sent after the caller joined.")
def search(group_id: UUID, q: str = Query(..., min_length=2), limit: int = Query(50, ge=1, le=100), db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return messages.search_messages(db, current_user, group_id, q, limit=limit)


@router.get("/{group_id}/files", summary="Shared files", description="Only files sent after the caller joined.")
def list_files(group_id: UUID, limit: int = Query(100, ge=1, le=200), db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return messages.list_files(db, current_user, group_id, limit=limit)


@router.get("/{group_id}/reactions", summary="Which reactions are allowed",
            description="`enabled` is false in a care group. In a program group only the listed emojis are allowed.")
def allowed_reactions(group_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return messages.allowed_reactions(db, current_user, group_id)


@router.patch("/{group_id}/messages/{message_id}", response_model=GroupMessageResponse, summary="Edit my message",
              description="Within 15 minutes of sending (a setting). It shows \"edited\" and the original is kept.")
def edit_message(group_id: UUID, message_id: UUID, payload: EditGroupMessage, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return messages.edit_message(db, current_user, group_id, message_id, payload)


@router.post("/{group_id}/messages/{message_id}/retract", response_model=GroupMessageResponse, summary="Retract my message",
             description="Hidden from the group (\"Message retracted\"). The original is kept in the secure record; nothing is deleted.")
def retract_message(group_id: UUID, message_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return messages.retract_message(db, current_user, group_id, message_id)


@router.post("/{group_id}/messages/{message_id}/hide", response_model=GroupMessageResponse, summary="Remove any message (moderation)",
             description="Owner or admin, with a reason. A \"Message removed by admin\" note stays in its place and the original is kept.")
def hide_message(group_id: UUID, message_id: UUID, payload: HideMessage, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return messages.hide_message(db, current_user, group_id, message_id, payload)


@router.put("/{group_id}/messages/{message_id}/pin", response_model=GroupMessageResponse, summary="Pin a message", description="Providers only.")
def pin_message(group_id: UUID, message_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return messages.pin_message(db, current_user, group_id, message_id, True)


@router.delete("/{group_id}/messages/{message_id}/pin", response_model=GroupMessageResponse, summary="Unpin a message")
def unpin_message(group_id: UUID, message_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return messages.pin_message(db, current_user, group_id, message_id, False)


@router.put("/{group_id}/messages/{message_id}/reactions", summary="React to a message",
            description="Program groups only, from the allowed set. 403 in a care group.")
def add_reaction(group_id: UUID, message_id: UUID, payload: ReactionBody, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return messages.react(db, current_user, group_id, message_id, payload.emoji, add=True)


@router.delete("/{group_id}/messages/{message_id}/reactions", summary="Remove my reaction")
def remove_reaction(group_id: UUID, message_id: UUID, emoji: str = Query(..., max_length=16), db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return messages.react(db, current_user, group_id, message_id, emoji, add=False)


@router.get("/{group_id}/messages/{message_id}/read-status", response_model=ReadStatusResponse, summary="Who has seen a message",
            description="\"seen by 4 of 6\". In a care group everyone sees who; in a program group patients get 403 and providers see it.")
def read_status(group_id: UUID, message_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return messages.read_status(db, current_user, group_id, message_id)


@router.get("/{group_id}/messages/{message_id}/original", response_model=OriginalMessageResponse, summary="The original, from the secure record",
            description="Group owner and compliance only. Shows the text as first written, every earlier version, and who hid it and why. Each read is audited.")
def original_message(request: Request, group_id: UUID, message_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return messages.original_message(db, current_user, group_id, message_id, token(request))


@router.post("/{group_id}/messages/{message_id}/save-to-record", response_model=ClinicalRecordResponse, status_code=status.HTTP_201_CREATED,
             summary="Save a message to a patient's clinical record", description="Providers only. A snapshot is kept.")
def save_to_record(request: Request, group_id: UUID, message_id: UUID, payload: SaveToRecord, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return messages.save_to_record(db, current_user, group_id, message_id, payload, token(request))


@router.get("/{group_id}/clinical-records", response_model=list[ClinicalRecordResponse], summary="Messages saved to clinical records",
            description="Providers of this group and compliance.")
def clinical_records(request: Request, group_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return messages.list_clinical_records(db, current_user, group_id, token(request))
