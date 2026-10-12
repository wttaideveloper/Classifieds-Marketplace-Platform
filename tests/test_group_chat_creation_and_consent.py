"""Creating a group, inviting people and consent.

Spec rules covered: only verified providers and clinic admins create groups; patients never do; every group is
linked to a program / appointment series / care episode; names must not include a patient's name or a health
condition; nobody is added without consent (and a parent's for someone under 18); an invited person sees nothing;
a person only sees what was sent after they joined; every group shows "N people can see this" and a
"not for emergencies" notice.
"""
from uuid import uuid4

import pytest

from tests.group_chat_support import (
    EPISODE_ID, PROGRAM_ID, accept, audit_actions, make_group, messages_of, say, texts, uid, world,  # noqa: F401
)

API = "/api/v1/groups"


def create(w, user, **body):
    base = {"name": "Yoga batch", "kind": "program", "context_type": "program", "context_id": str(PROGRAM_ID), "invitees": []}
    return w.as_user(user).post(API, json={**base, **body})


# --- who may create ---------------------------------------------------------------------------------------------

def test_a_verified_provider_can_create_a_group(world):
    resp = create(world, world.people.rao)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Yoga batch" and body["kind"] == "program" and body["my_role"] == "owner" and body["my_kind"] == "provider"
    assert body["created_by"] == world.people.rao["id"] and body["status"] == "open"


def test_a_clinic_admin_can_create_a_group(world):
    assert create(world, world.people.admin).status_code == 201


def test_a_patient_can_never_create_a_group(world):
    resp = create(world, world.people.priya)

    assert resp.status_code == 403
    assert world.db.query(__import__("app.models.chat_model", fromlist=["Conversation"]).Conversation).count() == 0


def test_a_provider_whose_registration_is_not_verified_cannot_create_one(world):
    resp = create(world, world.people.unverified)

    assert resp.status_code == 403 and "not been verified" in resp.json()["detail"]


def test_a_super_admin_does_not_create_groups(world):
    assert create(world, world.people.root).status_code == 403


def test_verification_can_be_switched_off_by_setting(world, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "GROUP_REQUIRE_VERIFIED_PROVIDER", False)

    assert create(world, world.people.unverified).status_code == 201


# --- the link ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("missing", ["context_type", "context_id"])
def test_a_group_must_be_linked_to_a_program_or_episode(world, missing):
    body = {"name": "Yoga batch", "kind": "program", "context_type": "program", "context_id": str(PROGRAM_ID)}
    body.pop(missing)

    assert world.as_user(world.people.rao).post(API, json=body).status_code == 422


def test_the_link_type_must_be_one_of_the_three(world):
    assert create(world, world.people.rao, context_type="shopping_cart").status_code == 422


@pytest.mark.parametrize("link_type", ["program", "appointment_series", "care_episode"])
def test_all_three_link_types_work(world, link_type):
    assert create(world, world.people.rao, context_type=link_type).status_code == 201


def test_a_link_that_does_not_exist_is_refused(world, monkeypatch):
    from app.services import group_chat_service

    monkeypatch.setattr(group_chat_service, "_link_exists", lambda *a, **k: False)

    resp = create(world, world.people.rao)

    assert resp.status_code == 422 and "not found" in resp.json()["detail"]


def test_kind_must_be_care_or_program(world):
    assert create(world, world.people.rao, kind="social").status_code == 422


# --- names ------------------------------------------------------------------------------------------------------

def test_a_name_with_a_patients_name_is_refused(world):
    resp = create(world, world.people.rao, name="Priya's recovery", invitees=[{"user_id": world.people.priya["id"]}])

    assert resp.status_code == 422 and "patient's name" in resp.json()["detail"]


def test_a_name_with_a_health_condition_is_refused(world):
    resp = create(world, world.people.rao, name="Diabetes support circle")

    assert resp.status_code == 422 and "health condition" in resp.json()["detail"]


def test_a_blocked_term_inside_another_word_is_fine(world):
    """Whole words only: "cancer" is blocked, "Cancerian" is not."""
    assert create(world, world.people.rao, name="Cancerian weekend club").status_code == 201
    assert create(world, world.people.rao, name="Weekend yoga").status_code == 201
    assert create(world, world.people.rao, name="Cancer support").status_code == 422


def test_a_blank_name_is_refused(world):
    assert create(world, world.people.rao, name="   ").status_code == 422


def test_renaming_follows_the_same_rule_and_is_logged(world):
    group = make_group(world, invitees=[world.people.priya])
    resp = world.as_user(world.people.rao).patch(f"{API}/{group['id']}", json={"name": "Priya's batch"})
    assert resp.status_code == 422

    ok = world.as_user(world.people.rao).patch(f"{API}/{group['id']}", json={"name": "Morning batch", "description": "Updated"})

    assert ok.status_code == 200 and ok.json()["name"] == "Morning batch" and ok.json()["description"] == "Updated"
    assert "group_updated" in audit_actions(world, group["id"])


# --- invitations: nobody is added without consent ---------------------------------------------------------------

def test_an_invited_person_is_not_a_member_and_sees_nothing(world):
    group = make_group(world, invitees=[world.people.priya])
    priya = world.people.priya

    assert world.as_user(priya).get(f"{API}/{group['id']}").status_code == 403
    assert world.as_user(priya).get(f"{API}/{group['id']}/messages").status_code == 403
    assert world.as_user(priya).get(f"{API}/{group['id']}/members").status_code == 403
    assert world.as_user(priya).post(f"{API}/{group['id']}/messages", json={"content": "hi"}).status_code == 403
    assert world.as_user(priya).get(API).json()["items"] == []  # not in "my groups"
    assert world.as_user(priya).post(f"{API}/{group['id']}/read").status_code == 403


def test_the_group_shows_the_invitee_in_pending_invitations(world):
    group = make_group(world, invitees=[world.people.priya])

    invitations = world.as_user(world.people.priya).get(f"{API}/invitations").json()

    assert invitations["total"] == 1 and invitations["items"][0]["conversation_id"] == group["id"]


def test_the_invitation_explains_the_group_without_revealing_what_was_said(world):
    group = make_group(world, kind="program", invitees=[world.people.priya, world.people.ravi], description="Weekly yoga")
    say(world, world.people.rao, group["id"], "private clinical detail")

    one = world.as_user(world.people.priya).get(f"{API}/invitations/{group['id']}").json()

    assert one["name"] == "Yoga batch Oct 2026" and one["kind"] == "program" and one["description"] == "Weekly yoga"
    assert one["provider_names"] == ["Dr Rao"] and one["invited_by_name"] == "Dr Rao"
    assert one["member_count"] == 1 and one["visibility_notice"] == "1 person can see this"
    assert "display name" in one["what_members_can_see"] and "leave at any time" in one["what_members_can_see"]
    assert "Not for emergencies" in one["emergency_notice"]["text"] and one["emergency_notice"]["number"] == "112"
    assert "private clinical detail" not in str(one) and "Ravi" not in str(one)  # no other patient is named


def test_an_invitation_is_a_notification_with_no_message_content(world):
    make_group(world, invitees=[world.people.priya])

    [n] = [n for n in world.notifications if n["category"] == "group_invitation"]

    assert n["user_ids"] == [uid(world.people.priya)]
    assert "invited" in n["title"] and n["metadata"]["conversation_id"]
    assert "Yoga" not in n["title"] + n["message"]  # not even the group name on a lock screen


def test_accepting_needs_consent_true(world):
    group = make_group(world, invitees=[world.people.priya])

    resp = world.as_user(world.people.priya).post(f"{API}/{group['id']}/accept", json={"consent": False})

    assert resp.status_code == 422
    assert world.as_user(world.people.priya).get(f"{API}/{group['id']}").status_code == 403


def test_accepting_makes_the_person_a_member_and_records_the_consent(world):
    group = make_group(world, invitees=[world.people.priya])

    body = accept(world, world.people.priya, group["id"], display_name="Priya S.")

    assert body["my_role"] == "member" and body["my_kind"] == "patient" and body["member_count"] == 2
    assert world.as_user(world.people.priya).get(f"{API}").json()["total"] == 1
    consents = world.as_user(world.people.priya).get(f"{API}/consents/me").json()["items"]
    assert len(consents) == 1 and consents[0]["conversation_id"] == group["id"] and consents[0]["withdrawn_at"] is None
    assert consents[0]["notice"]["visibility_notice"] and consents[0]["group_name"] == "Yoga batch Oct 2026"
    assert "consent_granted" in audit_actions(world, group["id"])


def test_someone_under_18_needs_a_parent_or_guardians_consent(world):
    group = make_group(world, invitees=[{"user_id": world.people.ravi["id"], "is_minor": True}])
    assert world.as_user(world.people.priya).get(f"{API}/invitations/{group['id']}").status_code == 404  # not hers
    invitation = world.as_user(world.people.ravi).get(f"{API}/invitations/{group['id']}").json()
    assert invitation["requires_guardian_consent"] is True

    alone = world.as_user(world.people.ravi).post(f"{API}/{group['id']}/accept", json={"consent": True})
    assert alone.status_code == 422 and "guardian" in alone.json()["detail"]

    ok = world.as_user(world.people.ravi).post(
        f"{API}/{group['id']}/accept", json={"consent": True, "guardian": {"name": "Meena Kumar", "relationship": "mother"}})
    assert ok.status_code == 200
    consent = world.as_user(world.people.ravi).get(f"{API}/consents/me").json()["items"][0]
    assert consent["is_minor"] is True and consent["guardian_name"] == "Meena Kumar" and consent["guardian_relationship"] == "mother"


def test_declining_keeps_the_person_out(world):
    group = make_group(world, invitees=[world.people.priya])

    resp = world.as_user(world.people.priya).post(f"{API}/{group['id']}/decline", json={"reason": "not for me"})

    assert resp.status_code == 200 and resp.json()["status"] == "declined"
    assert world.as_user(world.people.priya).get(f"{API}/invitations").json()["total"] == 0
    assert world.as_user(world.people.priya).get(f"{API}/{group['id']}").status_code == 403
    assert "consent_declined" in audit_actions(world, group["id"])


def test_a_stranger_cannot_accept_someone_elses_invitation(world):
    group = make_group(world, invitees=[world.people.priya])

    assert world.as_user(world.people.stranger).post(f"{API}/{group['id']}/accept", json={"consent": True}).status_code == 404


def test_an_invitee_cannot_accept_twice_or_after_leaving(world):
    group = make_group(world, invitees=[world.people.priya])
    accept(world, world.people.priya, group["id"])

    assert world.as_user(world.people.priya).post(f"{API}/{group['id']}/accept", json={"consent": True}).status_code == 404


def test_there_are_no_invite_links(world):
    """People are added only by a named invitation: no route creates or follows a link."""
    paths = [p for p in world.app.openapi()["paths"] if p.startswith(API)]

    assert len(paths) > 20  # the group routes are really there, so this is not an empty check
    assert not [p for p in paths if any(word in p.lower() for word in ("invite-link", "invite_link", "join", "link", "token", "code"))]


def test_people_can_be_invited_again_after_declining(world):
    group = make_group(world, invitees=[world.people.priya])
    world.as_user(world.people.priya).post(f"{API}/{group['id']}/decline", json={})

    again = world.as_user(world.people.rao).post(f"{API}/{group['id']}/members", json={"invitees": [{"user_id": world.people.priya["id"]}]})

    assert again.status_code == 201 and again.json()["invited"] == [world.people.priya["id"]]
    accept(world, world.people.priya, group["id"])


def test_inviting_someone_already_invited_or_in_is_skipped(world):
    group = make_group(world, invitees=[world.people.priya])

    resp = world.as_user(world.people.rao).post(f"{API}/{group['id']}/members", json={"invitees": [{"user_id": world.people.priya["id"]}]})

    assert resp.json() == {"invited": [], "skipped": [world.people.priya["id"]]}


def test_only_an_owner_or_admin_can_invite(world):
    group = make_group(world, invitees=[world.people.priya])
    accept(world, world.people.priya, group["id"])

    resp = world.as_user(world.people.priya).post(f"{API}/{group['id']}/members", json={"invitees": [{"user_id": world.people.ravi["id"]}]})

    assert resp.status_code == 403


# --- what a member sees after joining ---------------------------------------------------------------------------

def test_a_new_member_sees_only_what_was_sent_after_they_joined(world):
    group = make_group(world, kind="care", invitees=[world.people.priya])
    say(world, world.people.rao, group["id"], "before priya joined")
    accept(world, world.people.priya, group["id"])
    say(world, world.people.rao, group["id"], "after priya joined")

    body = messages_of(world, world.people.priya, group["id"])

    assert "after priya joined" in texts(body) and "before priya joined" not in texts(body)
    assert "before priya joined" in texts(messages_of(world, world.people.rao, group["id"]))  # the owner still sees it


def test_system_lines_name_people_but_carry_no_health_information(world):
    group = make_group(world, invitees=[world.people.priya])
    accept(world, world.people.priya, group["id"])

    owner_view = texts(messages_of(world, world.people.rao, group["id"]))

    assert "Dr Rao created the group" in owner_view
    assert any(t.startswith("Dr Rao added Priya") for t in owner_view)
    assert not any("Sharma" in t for t in owner_view)  # a patient is a display name, not a full name


# --- group sizes -------------------------------------------------------------------------------------------------

def test_a_care_group_holds_at_most_15_people(world):
    invitees = [{"user_id": str(uuid4())} for _ in range(15)]  # owner + 15 = 16

    resp = create(world, world.people.rao, kind="care", context_type="care_episode", context_id=str(EPISODE_ID), invitees=invitees)

    assert resp.status_code == 409 and "at most 15" in resp.json()["detail"]
    ok = create(world, world.people.rao, kind="care", context_type="care_episode", context_id=str(EPISODE_ID), invitees=invitees[:14])
    assert ok.status_code == 201


def test_a_program_group_holds_at_most_50_people(world):
    resp = create(world, world.people.rao, invitees=[{"user_id": str(uuid4())} for _ in range(50)])

    assert resp.status_code == 409 and "at most 50" in resp.json()["detail"]


def test_pending_invitations_count_against_the_limit(world):
    group = make_group(world, kind="care", invitees=[{"user_id": str(uuid4())} for _ in range(14)])

    resp = world.as_user(world.people.rao).post(f"{API}/{group['id']}/members", json={"invitees": [{"user_id": world.people.priya["id"]}]})

    assert resp.status_code == 409


def test_the_limits_are_settings(world, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "GROUP_MAX_MEMBERS_CARE", 3)

    resp = create(world, world.people.rao, kind="care", invitees=[{"user_id": str(uuid4())} for _ in range(3)])

    assert resp.status_code == 409 and "at most 3" in resp.json()["detail"]


# --- the notices on every group ---------------------------------------------------------------------------------

def test_every_group_says_how_many_people_can_see_it_and_not_for_emergencies(world):
    group = make_group(world, invitees=[world.people.priya, world.people.ravi])
    accept(world, world.people.priya, group["id"])

    body = world.as_user(world.people.rao).get(f"{API}/{group['id']}").json()

    assert body["member_count"] == 2 and body["visibility_notice"] == "2 people can see this"  # invitees are not counted
    assert body["emergency_notice"] == {"text": "Not for emergencies. In an emergency call 112.", "number": "112"}
    assert body["forwarding_blocked"] is True and body["screenshots_blocked"] is True
    assert body["limits"] == {"max_members": 50, "max_admins": 3, "edit_window_minutes": 15}


def test_admins_only_posting_defaults_on_for_program_off_for_care(world):
    program = make_group(world, kind="program")
    care = make_group(world, kind="care")

    assert program["admins_only_posting"] is True and care["admins_only_posting"] is False
    explicit = make_group(world, kind="program", admins_only_posting=False)
    assert explicit["admins_only_posting"] is False


def test_creation_is_in_the_audit_trail(world):
    group = make_group(world, invitees=[world.people.priya])

    actions = audit_actions(world, group["id"])

    assert "group_created" in actions and "member_invited" in actions
