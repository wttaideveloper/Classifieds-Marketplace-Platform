"""Compliance: the audit trail, reports, legal hold, what patients agreed to, their data requests, sharing earlier
history in a care group, verified providers, and how groups fit alongside the existing 1:1 chat.

"Compliance" = the clinic's Enterprise Admin (for that clinic's groups) or the platform's Super Admin.
"""
from uuid import UUID, uuid4

import pytest

from app.models.chat_model import Conversation, ConversationParticipant, Message
from app.models.group_chat_model import GroupAuditLog, VerifiedProvider
from tests.group_chat_support import (
    TENANT, accept, audit_actions, joined_group, make_group, messages_of, person, say, texts, uid, world,  # noqa: F401
)

API = "/api/v1/groups"


@pytest.fixture
def care(world):
    p = world.people
    return joined_group(world, kind="care", members=[p.mehta, p.priya])


@pytest.fixture
def program(world):
    p = world.people
    return joined_group(world, kind="program", members=[p.mehta, p.priya, p.ravi])


# =============================================================================================================
# the audit trail: read only
# =============================================================================================================

def test_the_trail_records_the_admin_actions_in_order_newest_first(world, care):
    p = world.people
    world.as_user(p.rao).patch(f"{API}/{care['id']}/members/{p.mehta['id']}/role", json={"role": "admin"})
    world.as_user(p.rao).patch(f"{API}/{care['id']}", json={"description": "New focus"})
    world.as_user(p.rao).post(f"{API}/{care['id']}/close", json={"reason": "episode ended"})

    items = world.as_user(p.rao).get(f"{API}/{care['id']}/audit").json()["items"]

    actions = [e["action"] for e in items]
    assert actions.index("group_closed") < actions.index("group_updated") < actions.index("role_changed") < actions.index("group_created")
    closed = items[0]
    assert closed["action"] == "group_closed" and closed["reason"] == "episode ended" and closed["actor_id"] == p.rao["id"]
    assert closed["actor_role"] == "provider" and closed["conversation_id"] == care["id"]


def test_it_records_consent_role_changes_and_edits(world, care):
    p = world.people
    msg = say(world, p.priya, care["id"], "first version").json()
    world.as_user(p.priya).patch(f"{API}/{care['id']}/messages/{msg['id']}", json={"content": "second version"})

    actions = audit_actions(world, care["id"])

    assert {"group_created", "member_invited", "consent_granted", "message_edited"} <= set(actions)
    assert actions.count("consent_granted") == 2


def test_the_trail_never_contains_message_text(world, care):
    p = world.people
    msg = say(world, p.priya, care["id"], "my diagnosis is confidential").json()
    world.as_user(p.priya).patch(f"{API}/{care['id']}/messages/{msg['id']}", json={"content": "my new diagnosis is confidential"})
    world.as_user(p.rao).post(f"{API}/{care['id']}/messages/{msg['id']}/hide", json={"reason": "shared outside the team"})

    raw = world.as_user(p.rao).get(f"{API}/{care['id']}/audit").text

    assert "diagnosis" not in raw and "confidential" not in raw


def test_it_can_be_filtered(world, care):
    p = world.people
    world.as_user(p.rao).patch(f"{API}/{care['id']}/members/{p.mehta['id']}/role", json={"role": "admin"})

    only_roles = world.as_user(p.rao).get(f"{API}/{care['id']}/audit", params={"action": "role_changed"}).json()
    about_mehta = world.as_user(p.rao).get(f"{API}/{care['id']}/audit", params={"target_user_id": p.mehta["id"]}).json()

    assert only_roles["total"] == 1 and {e["action"] for e in about_mehta["items"]} >= {"role_changed", "member_invited", "consent_granted"}


def test_the_trail_is_paged(world, care):
    p = world.people
    for _ in range(5):
        world.as_user(p.rao).patch(f"{API}/{care['id']}", json={"description": uuid4().hex})

    page = world.as_user(p.rao).get(f"{API}/{care['id']}/audit", params={"page": 2, "page_size": 3}).json()

    assert len(page["items"]) == 3 and page["total"] >= 9


def test_who_can_read_the_trail(world, care):
    p = world.people
    url = f"{API}/{care['id']}/audit"

    for reader in (p.rao, p.admin, p.root):
        assert world.as_user(reader).get(url).status_code == 200
    for reader in (p.mehta, p.priya, p.outsider_admin, p.stranger):
        assert world.as_user(reader).get(url).status_code == 403


def test_the_trail_has_no_way_to_change_or_delete_an_entry(world):
    """Read-only means there is no route that writes to it."""
    paths = world.app.openapi()["paths"]
    audit = {path: set(methods) for path, methods in paths.items() if path.endswith("/audit")}

    assert audit and all(methods == {"get"} for methods in audit.values()), audit


def test_nothing_in_the_service_layer_updates_or_deletes_the_trail():
    import pathlib

    sources = "\n".join(pathlib.Path(f"app/services/{name}.py").read_text(encoding="utf-8") for name in (
        "group_chat_common", "group_chat_service", "group_chat_messages", "group_chat_compliance", "group_chat_notify"))

    assert "db.delete(" not in sources.replace("db.delete(existing)", "")  # the only delete is taking back a reaction
    assert "GroupAuditLog).update" not in sources and "GroupAuditLog).delete" not in sources
    assert "delete(GroupAuditLog" not in sources


def test_every_entry_is_written_with_the_change_in_one_transaction(world, care):
    """If saving the change fails, no audit row is left behind (and the other way round)."""
    from app.services import group_chat_service

    p = world.people
    before = world.db.query(GroupAuditLog).count()
    original_commit = world.db.commit

    def failing_commit():
        world.db.rollback()
        raise RuntimeError("database went away")

    world.db.commit = failing_commit
    try:
        with pytest.raises(RuntimeError):
            group_chat_service.update_group(world.db, p.rao, UUID(care["id"]), type("P", (), {"name": None, "description": "x", "avatar_url": None})())
    finally:
        world.db.commit = original_commit

    assert world.db.query(GroupAuditLog).count() == before
    assert world.db.get(Conversation, UUID(care["id"])).description != "x"


# =============================================================================================================
# reports
# =============================================================================================================

def test_any_member_can_report_a_message_and_compliance_is_told(world, care):
    p = world.people
    msg = say(world, p.mehta, care["id"], "something off").json()
    world.notifications.clear()

    resp = world.as_user(p.priya).post(f"{API}/{care['id']}/reports", json={"message_id": msg["id"], "reason": "privacy", "details": "names another patient"})

    assert resp.status_code == 201
    report = resp.json()
    assert report["status"] == "open" and report["reporter_id"] == p.priya["id"] and report["message_id"] == msg["id"]
    [notice] = [n for n in world.notifications if n["category"] == "group_report_filed"]
    assert notice["metadata"]["report_id"] == report["id"] and notice["message"] == "A message was reported."
    assert "something off" not in str(notice)  # the report notice carries no message text
    assert "report_filed" in audit_actions(world, care["id"])


def test_a_group_can_be_reported_as_a_whole(world, care):
    resp = world.as_user(world.people.priya).post(f"{API}/{care['id']}/reports", json={"reason": "safety"})

    assert resp.status_code == 201 and resp.json()["message_id"] is None


def test_you_cannot_report_a_message_from_before_you_joined_or_another_groups(world):
    p = world.people
    group = make_group(world, kind="care", invitees=[p.priya])
    early = say(world, p.rao, group["id"], "early").json()
    accept(world, p.priya, group["id"])
    other = joined_group(world, kind="care", members=[p.priya], name="Another team")
    foreign = say(world, p.rao, other["id"], "elsewhere").json()

    assert world.as_user(p.priya).post(f"{API}/{group['id']}/reports", json={"message_id": early["id"], "reason": "spam"}).status_code == 404
    assert world.as_user(p.priya).post(f"{API}/{group['id']}/reports", json={"message_id": foreign["id"], "reason": "spam"}).status_code == 404


def test_only_members_can_report(world, care):
    assert world.as_user(world.people.stranger).post(f"{API}/{care['id']}/reports", json={"reason": "spam"}).status_code == 403


def test_the_reason_must_be_a_known_one(world, care):
    assert world.as_user(world.people.priya).post(f"{API}/{care['id']}/reports", json={"reason": "i dislike it"}).status_code == 422


def test_compliance_sees_reports_of_its_own_scope(world, care, program):
    p = world.people
    world.as_user(p.priya).post(f"{API}/{care['id']}/reports", json={"reason": "spam"})
    world.as_user(p.ravi).post(f"{API}/{program['id']}/reports", json={"reason": "harassment"})

    clinic = world.as_user(p.admin).get(f"{API}/reports").json()
    platform = world.as_user(p.root).get(f"{API}/reports").json()
    other_clinic = world.as_user(p.outsider_admin).get(f"{API}/reports").json()
    filtered = world.as_user(p.admin).get(f"{API}/reports", params={"group_id": care["id"]}).json()

    assert clinic["total"] == 2 and platform["total"] == 2
    assert other_clinic["total"] == 0  # another clinic never sees this clinic's reports
    assert filtered["total"] == 1 and filtered["items"][0]["reason"] == "spam"


def test_patients_and_providers_cannot_list_reports(world, care):
    p = world.people
    for user in (p.priya, p.rao, p.mehta, p.stranger):
        assert world.as_user(user).get(f"{API}/reports").status_code == 403


def test_a_report_is_worked_through_and_resolved(world, care):
    p = world.people
    report = world.as_user(p.priya).post(f"{API}/{care['id']}/reports", json={"reason": "privacy"}).json()

    reviewing = world.as_user(p.admin).patch(f"{API}/reports/{report['id']}", json={"status": "reviewing"}).json()
    resolved = world.as_user(p.admin).patch(f"{API}/reports/{report['id']}", json={"status": "resolved", "note": "message removed, member reminded"}).json()

    assert reviewing["status"] == "reviewing" and reviewing["resolved_at"] is None
    assert resolved["status"] == "resolved" and resolved["resolution_note"] == "message removed, member reminded"
    assert resolved["resolved_by"] == p.admin["id"] and resolved["resolved_at"]
    assert "report_resolved" in audit_actions(world, care["id"])


def test_another_clinic_cannot_resolve_this_clinics_report(world, care):
    p = world.people
    report = world.as_user(p.priya).post(f"{API}/{care['id']}/reports", json={"reason": "privacy"}).json()

    assert world.as_user(p.outsider_admin).patch(f"{API}/reports/{report['id']}", json={"status": "dismissed"}).status_code == 404
    assert world.as_user(p.priya).patch(f"{API}/reports/{report['id']}", json={"status": "dismissed"}).status_code == 403


# =============================================================================================================
# legal hold
# =============================================================================================================

def test_compliance_can_put_a_group_on_legal_hold_and_lift_it(world, care):
    p = world.people

    held = world.as_user(p.admin).post(f"{API}/{care['id']}/legal-hold", json={"reason": "regulator request 2026-114"})
    assert held.status_code == 200 and held.json()["legal_hold"] is True
    assert world.as_user(p.rao).get(f"{API}/{care['id']}").json()["legal_hold"] is True
    lifted = world.as_user(p.root).delete(f"{API}/{care['id']}/legal-hold")
    assert lifted.status_code == 200 and lifted.json()["legal_hold"] is False
    assert {"legal_hold_set", "legal_hold_cleared"} <= set(audit_actions(world, care["id"]))


def test_only_compliance_can_set_a_legal_hold(world, care):
    p = world.people
    for user in (p.rao, p.mehta, p.priya, p.outsider_admin):
        assert world.as_user(user).post(f"{API}/{care['id']}/legal-hold", json={"reason": "no right"}).status_code == 403
    assert world.as_user(p.admin).post(f"{API}/{care['id']}/legal-hold", json={"reason": "x"}).status_code == 422


def test_retention_runs_from_the_day_the_group_closes(world, care):
    p = world.people
    before = world.as_user(p.rao).get(f"{API}/{care['id']}").json()
    assert before["retention_started_at"] is None and before["retention_until"] is None

    closed = world.as_user(p.rao).post(f"{API}/{care['id']}/close", json={}).json()

    assert closed["retention_started_at"] and closed["retention_until"] > closed["retention_started_at"]


def test_retention_is_left_blank_until_legal_sets_it(world, care, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "GROUP_RETENTION_DAYS", None)

    closed = world.as_user(world.people.rao).post(f"{API}/{care['id']}/close", json={}).json()

    assert closed["retention_started_at"] and closed["retention_until"] is None


def test_nothing_is_ever_deleted_a_hidden_message_is_still_a_row(world, care):
    p = world.people
    msg = say(world, p.priya, care["id"], "to be retracted").json()
    world.as_user(p.priya).post(f"{API}/{care['id']}/messages/{msg['id']}/retract")
    edited = say(world, p.priya, care["id"], "v1").json()
    world.as_user(p.priya).patch(f"{API}/{care['id']}/messages/{edited['id']}", json={"content": "v2"})

    rows = {m.content: m for m in world.db.query(Message).filter(Message.conversation_id == UUID(care["id"])).all()}

    assert "to be retracted" in rows and rows["to be retracted"].is_deleted is False  # still stored, only hidden
    assert rows["v2"].edit_history[0]["content"] == "v1"


# =============================================================================================================
# the compliance list of groups
# =============================================================================================================

def test_compliance_lists_groups_without_messages(world, care, program):
    p = world.people
    say(world, p.rao, care["id"], "private clinical detail")

    clinic = world.as_user(p.admin).get(f"{API}/compliance/groups").json()
    platform = world.as_user(p.root).get(f"{API}/compliance/groups").json()

    assert clinic["total"] == 2 and platform["total"] == 2
    assert {g["kind"] for g in clinic["items"]} == {"care", "program"}
    assert "private clinical detail" not in str(clinic) and all(g["unread_count"] == 0 and g["last_message_preview"] is None for g in clinic["items"])
    assert world.as_user(p.outsider_admin).get(f"{API}/compliance/groups").json()["total"] == 0
    assert world.as_user(p.rao).get(f"{API}/compliance/groups").status_code == 403


def test_compliance_filters_by_kind_status_and_hold(world, care, program):
    p = world.people
    world.as_user(p.rao).post(f"{API}/{program['id']}/close", json={})
    world.as_user(p.admin).post(f"{API}/{care['id']}/legal-hold", json={"reason": "audit"})

    assert [g["kind"] for g in world.as_user(p.admin).get(f"{API}/compliance/groups", params={"kind": "care"}).json()["items"]] == ["care"]
    assert [g["status"] for g in world.as_user(p.admin).get(f"{API}/compliance/groups", params={"status": "closed"}).json()["items"]] == ["closed"]
    assert [g["id"] for g in world.as_user(p.admin).get(f"{API}/compliance/groups", params={"legal_hold": True}).json()["items"]] == [care["id"]]


def test_compliance_can_read_a_groups_details_but_not_its_messages(world, care):
    p = world.people
    say(world, p.priya, care["id"], "patient detail")

    assert world.as_user(p.admin).get(f"{API}/{care['id']}").status_code == 200
    assert world.as_user(p.root).get(f"{API}/{care['id']}").json()["unread_count"] == 0
    assert world.as_user(p.admin).get(f"{API}/{care['id']}/messages").status_code == 403
    assert world.as_user(p.outsider_admin).get(f"{API}/{care['id']}").status_code == 403


# =============================================================================================================
# patients: what I agreed to, my data, who to complain to
# =============================================================================================================

def test_a_patient_sees_everything_they_agreed_to(world):
    p = world.people
    one = joined_group(world, kind="program", members=[p.priya], name="Morning yoga")
    two = joined_group(world, kind="care", members=[p.priya], name="Recovery team")
    world.as_user(p.priya).post(f"{API}/{one['id']}/consent/withdraw")

    items = world.as_user(p.priya).get(f"{API}/consents/me").json()["items"]

    assert {c["group_name"]: c["withdrawn_at"] is None for c in items} == {"Morning yoga": False, "Recovery team": True}
    assert all(c["notice"]["what_members_can_see"] and c["notice"]["emergency_notice"] for c in items)
    assert world.as_user(p.ravi).get(f"{API}/consents/me").json()["items"] == []  # nobody else's


def test_a_patient_can_ask_for_a_copy_of_their_data(world, care):
    p = world.people

    everything = world.as_user(p.priya).post(f"{API}/data-requests", json={"note": "for my records"})
    one_group = world.as_user(p.priya).post(f"{API}/data-requests", json={"conversation_id": care["id"]})

    assert everything.status_code == 201 and everything.json()["status"] == "received" and everything.json()["conversation_id"] is None
    assert one_group.json()["conversation_id"] == care["id"]
    assert len(world.as_user(p.priya).get(f"{API}/data-requests/me").json()) == 2
    assert world.as_user(p.ravi).get(f"{API}/data-requests/me").json() == []


def test_a_data_request_for_a_group_you_were_never_in_is_refused(world, care):
    assert world.as_user(world.people.stranger).post(f"{API}/data-requests", json={"conversation_id": care["id"]}).status_code == 404


def test_a_former_member_can_still_ask_for_their_data(world, care):
    p = world.people
    world.as_user(p.priya).post(f"{API}/{care['id']}/leave")

    assert world.as_user(p.priya).post(f"{API}/data-requests", json={"conversation_id": care["id"]}).status_code == 201


def test_the_grievance_officer_comes_from_settings(world, monkeypatch):
    from app.core.config import settings

    p = world.people
    assert world.as_user(p.priya).get(f"{API}/grievance-officer").json() == {"name": None, "email": None, "phone": None, "configured": False}

    monkeypatch.setattr(settings, "GROUP_GRIEVANCE_OFFICER_NAME", "A. Verma")
    monkeypatch.setattr(settings, "GROUP_GRIEVANCE_OFFICER_EMAIL", "grievance@clinic.test")

    body = world.as_user(p.priya).get(f"{API}/grievance-officer").json()
    assert body["name"] == "A. Verma" and body["email"] == "grievance@clinic.test" and body["configured"] is True


# =============================================================================================================
# sharing earlier history in a care group
# =============================================================================================================

def share(world, care, target, reason="second opinion needed"):
    return world.as_user(world.people.rao).post(f"{API}/{care['id']}/members/{target['id']}/share-history", json={"reason": reason})


def test_earlier_history_is_shared_only_with_the_patients_agreement_and_it_is_logged(world):
    p = world.people
    group = make_group(world, kind="care", invitees=[p.priya])
    say(world, p.rao, group["id"], "history before the second doctor")
    accept(world, p.priya, group["id"])
    world.as_user(p.rao).post(f"{API}/{group['id']}/members", json={"invitees": [{"user_id": p.mehta["id"]}]})
    accept(world, p.mehta, group["id"])
    assert "history before the second doctor" not in texts(messages_of(world, p.mehta, group["id"]))  # not by default

    refused = share(world, group, p.mehta)
    assert refused.status_code == 403 and "patient has not agreed" in refused.json()["detail"]
    assert world.as_user(p.priya).post(f"{API}/{group['id']}/history-sharing/consent").json() == {"conversation_id": group["id"], "agreed": True}
    ok = share(world, group, p.mehta)

    assert ok.status_code == 200
    assert "history before the second doctor" in texts(messages_of(world, p.mehta, group["id"]))
    entry = [e for e in world.as_user(p.rao).get(f"{API}/{group['id']}/audit").json()["items"] if e["action"] == "history_shared"][0]
    assert entry["reason"] == "second opinion needed" and entry["target_user_id"] == p.mehta["id"]
    assert {"history_sharing_agreed", "history_shared"} <= set(audit_actions(world, group["id"]))


def test_the_patient_can_take_their_agreement_back(world, care):
    p = world.people
    world.as_user(p.priya).post(f"{API}/{care['id']}/history-sharing/consent")

    assert world.as_user(p.priya).delete(f"{API}/{care['id']}/history-sharing/consent").json()["agreed"] is False
    assert share(world, care, p.mehta).status_code == 403


def test_only_the_owner_can_share_and_only_with_a_provider(world, care):
    p = world.people
    world.as_user(p.priya).post(f"{API}/{care['id']}/history-sharing/consent")

    assert world.as_user(p.mehta).post(f"{API}/{care['id']}/members/{p.mehta['id']}/share-history", json={"reason": "own reason"}).status_code == 403
    assert share(world, care, p.priya).status_code == 422  # a patient is not a care-team provider
    assert share(world, care, p.stranger).status_code == 404
    assert world.as_user(p.rao).post(f"{API}/{care['id']}/members/{p.mehta['id']}/share-history", json={"reason": "x"}).status_code == 422  # reason too short


def test_only_the_patient_can_agree(world, care):
    assert world.as_user(world.people.mehta).post(f"{API}/{care['id']}/history-sharing/consent").status_code == 403


def test_history_is_never_shared_in_a_program_group(world, program):
    p = world.people

    assert world.as_user(p.priya).post(f"{API}/{program['id']}/history-sharing/consent").status_code == 409
    assert world.as_user(p.rao).post(f"{API}/{program['id']}/members/{p.mehta['id']}/share-history", json={"reason": "because"}).status_code == 409


# =============================================================================================================
# verified providers
# =============================================================================================================

def test_a_clinic_admin_verifies_a_provider_of_their_own_clinic(world):
    p = world.people
    nobody = world.as_user(p.unverified).post(API, json={"name": "Try", "kind": "program", "context_type": "program", "context_id": str(uuid4())})
    assert nobody.status_code == 403

    resp = world.as_user(p.admin).post(f"{API}/verified-providers", json={
        "user_id": p.unverified["id"], "registration_number": "MH-12345", "registration_council": "State Medical Council"})

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "verified" and body["registration_number"] == "MH-12345" and body["verified_by"] == p.admin["id"]
    assert body["tenant_id"] == str(TENANT)
    again = world.as_user(p.unverified).post(API, json={"name": "Try", "kind": "program", "context_type": "program", "context_id": str(uuid4())})
    assert again.status_code == 201


def test_a_clinic_admin_cannot_verify_for_another_clinic(world):
    p = world.people

    resp = world.as_user(p.outsider_admin).post(f"{API}/verified-providers", json={"user_id": p.unverified["id"], "tenant_id": str(TENANT)})

    assert resp.status_code == 403


def test_the_platform_can_verify_for_any_clinic_and_providers_cannot_verify_themselves(world):
    p = world.people

    assert world.as_user(p.root).post(f"{API}/verified-providers", json={"user_id": p.unverified["id"], "tenant_id": str(TENANT)}).status_code == 201
    for user in (p.rao, p.unverified, p.priya):
        assert world.as_user(user).post(f"{API}/verified-providers", json={"user_id": p.unverified["id"]}).status_code == 403
        assert world.as_user(user).get(f"{API}/verified-providers").status_code == 403


def test_revoking_a_verification_stops_them_creating_groups(world):
    p = world.people
    assert world.as_user(p.admin).delete(f"{API}/verified-providers/{p.rao['id']}").json()["status"] == "revoked"

    resp = world.as_user(p.rao).post(API, json={"name": "New", "kind": "program", "context_type": "program", "context_id": str(uuid4())})

    assert resp.status_code == 403


def test_the_list_shows_the_clinics_providers_only(world):
    p = world.people
    mine = world.as_user(p.admin).get(f"{API}/verified-providers").json()
    theirs = world.as_user(p.outsider_admin).get(f"{API}/verified-providers").json()

    assert {row["user_id"] for row in mine} == {p.rao["id"], p.mehta["id"], p.nair["id"]} and theirs == []
    assert len(world.as_user(p.root).get(f"{API}/verified-providers").json()) == 3


def test_revoking_someone_who_was_never_verified_is_404(world):
    assert world.as_user(world.people.admin).delete(f"{API}/verified-providers/{uuid4()}").status_code == 404


# =============================================================================================================
# groups alongside the existing 1:1 chat
# =============================================================================================================

def test_a_patients_normal_inbox_leaves_groups_to_the_groups_tab(world, care):
    p = world.people

    inbox = world.as_user(p.priya).get("/api/v1/conversations/").json()["items"]
    tab = world.as_user(p.priya).get(API).json()["items"]

    assert inbox == [] and [g["id"] for g in tab] == [care["id"]]


def test_a_provider_has_one_inbox_with_groups_labelled(world, care):
    p = world.people

    items = world.as_user(p.mehta).get("/api/v1/conversations/").json()["items"]
    mine = [i for i in items if i["id"] == care["id"]]

    assert len(mine) == 1 and mine[0]["conversation_type"] == "group" and mine[0]["group_kind"] == "care" and mine[0]["subject"] == care["name"]
    assert mine[0]["customer_name"] is None  # a group has no single customer


def test_the_provider_list_also_shows_groups_with_no_customer_name(world, care):
    items = world.as_user(world.people.rao).get("/api/v1/conversations/provider").json()["items"]

    assert [i for i in items if i["id"] == care["id"]][0]["customer_name"] is None


def test_the_generic_detail_lists_only_active_members(world):
    p = world.people
    group = make_group(world, kind="care", invitees=[p.priya, p.ravi])
    accept(world, p.priya, group["id"])

    detail = world.as_user(p.rao).get(f"/api/v1/conversations/{group['id']}").json()

    assert {x["user_id"] for x in detail["participants"]} == {p.rao["id"], p.priya["id"]}  # Ravi has not agreed: not listed
    assert world.as_user(p.ravi).get(f"/api/v1/conversations/{group['id']}").status_code == 403


def test_the_generic_endpoints_cannot_be_used_to_create_a_group(world):
    resp = world.as_user(world.people.rao).post("/api/v1/conversations/", json={"conversation_type": "group", "subject": "Sneaky"})

    assert resp.status_code == 422


def test_a_one_to_one_chat_is_never_matched_to_a_group_with_the_same_people(world):
    p = world.people
    group = joined_group(world, kind="care", members=[p.priya])

    chat = world.as_user(p.rao).post("/api/v1/conversations/", json={"participant_ids": [{"user_id": p.priya["id"], "role": "customer"}]})

    assert chat.status_code == 201 and chat.json()["id"] != group["id"] and chat.json()["conversation_type"] == "standard"


def test_the_realtime_room_cannot_be_joined_without_being_an_active_member(world):
    from fastapi import HTTPException

    from app.services.socket_io_service import validate_join_room

    p = world.people
    group = make_group(world, kind="care", invitees=[p.priya, p.ravi])
    accept(world, p.priya, group["id"])
    world.as_user(p.rao).delete(f"{API}/{group['id']}/members/{p.priya['id']}")

    assert validate_join_room(world.db, p.rao, UUID(group["id"]))["authorized"] is True
    for outsider in (p.priya, p.ravi, p.stranger):  # removed, only invited, never invited
        with pytest.raises(HTTPException) as err:
            validate_join_room(world.db, outsider, UUID(group["id"]))
        assert err.value.status_code == 403


def test_one_to_one_chats_still_work_as_before(world):
    """Adding groups must not change a normal chat: same participants, same messages, same lists."""
    p = world.people
    chat = world.as_user(p.priya).post("/api/v1/conversations/", json={"participant_ids": [{"user_id": p.rao["id"], "role": "provider"}]}).json()

    sent = world.as_user(p.priya).post("/api/v1/messages/", json={"conversation_id": chat["id"], "content": "hello doctor"})
    listing = world.as_user(p.rao).get(f"/api/v1/conversations/{chat['id']}/messages")

    assert sent.status_code == 201 and sent.json()["content"] == "hello doctor"
    assert [m["content"] for m in listing.json()["items"]] == ["hello doctor"]
    assert [c["id"] for c in world.as_user(p.priya).get("/api/v1/conversations/").json()["items"]] == [chat["id"]]
    row = world.db.query(ConversationParticipant).filter_by(conversation_id=UUID(chat["id"]), user_id=uid(p.priya)).one()
    assert row.membership_status == "active"
