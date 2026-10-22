"""Members, roles, ownership, muting, closing: who may do what in a group, and what happens straight away.

Spec rules covered: removed people are told and lose access at once; anyone can leave (an owner hands over first);
only a verified provider can be an admin (never a patient); the creator is the owner and cannot be removed by other
admins; only the owner or compliance (with a reason) changes an admin; at most 3 admins; admins can mute someone
(timed or not); admins-only posting; closing makes the group read-only and starts retention; reopening needs the
owner and a reason; members can mute/pin a group for themselves without losing access.
"""
from datetime import datetime, timedelta

import pytest

from tests.group_chat_support import (
    accept, audit_actions, joined_group, make_group, messages_of, say, texts, uid, world,  # noqa: F401
)

API = "/api/v1/groups"


def members_by_name(w, viewer, gid):
    return {m["name"]: m for m in w.as_user(viewer).get(f"{API}/{gid}/members").json()["members"]}


@pytest.fixture
def care(world):
    """A care group: Dr Rao (owner), Dr Mehta (provider), Priya (patient)."""
    p = world.people
    return joined_group(world, kind="care", members=[p.mehta, p.priya])


@pytest.fixture
def program(world):
    """A program group: Dr Rao (owner), Dr Mehta, and patients Priya, Ravi, Asha."""
    p = world.people
    return joined_group(world, kind="program", members=[p.mehta, p.priya, p.ravi, p.asha])


# --- who is in the group and what each person sees of the others ----------------------------------------------

def test_providers_see_everyone_by_full_name(world, program):
    seen = members_by_name(world, world.people.rao, program["id"])

    assert set(seen) == {"Dr Rao", "Dr Mehta", "Priya Sharma", "Ravi Kumar", "Asha Iyer"}
    assert seen["Dr Rao"]["role"] == "owner" and seen["Priya Sharma"]["kind"] == "patient"


def test_in_a_program_group_a_patient_sees_other_patients_by_display_name_only(world, program):
    seen = members_by_name(world, world.people.priya, program["id"])

    assert "Ravi Kumar" not in seen and "Asha Iyer" not in seen
    assert {"Ravi K.", "Asha I."} <= set(seen)  # first name + initial, never a full name
    assert "Dr Rao" in seen and "Dr Mehta" in seen  # the providers running it are named in full
    assert seen["Priya Sharma"]["is_me"] is True


def test_a_display_name_the_patient_chose_is_what_others_see(world):
    p = world.people
    group = make_group(world, kind="program", invitees=[p.priya, p.ravi])
    accept(world, p.priya, group["id"], display_name="Pri")
    accept(world, p.ravi, group["id"])

    assert "Pri" in members_by_name(world, p.ravi, group["id"])


def test_an_email_cannot_be_used_as_a_display_name(world):
    p = world.people
    group = make_group(world, kind="program", invitees=[p.priya, p.ravi])
    accept(world, p.priya, group["id"], display_name="priya@example.test")
    accept(world, p.ravi, group["id"])

    assert "priya@example.test" not in members_by_name(world, p.ravi, group["id"])
    assert "Priya S." in members_by_name(world, p.ravi, group["id"])


def test_in_a_care_group_everyone_is_named_in_full(world, care):
    assert {"Dr Rao", "Dr Mehta", "Priya Sharma"} <= set(members_by_name(world, world.people.priya, care["id"]))


def test_the_member_list_has_no_phone_or_profile_details(world, program):
    raw = world.as_user(world.people.priya).get(f"{API}/{program['id']}/members").text

    for leaked in ("phone", "email", "@example.test", "address"):
        assert leaked not in raw


def test_pending_invitees_are_visible_to_admins_only(world):
    p = world.people
    group = make_group(world, kind="program", invitees=[p.priya, p.ravi])
    accept(world, p.priya, group["id"])

    admin_view = world.as_user(p.rao).get(f"{API}/{group['id']}/members").json()["members"]
    member_view = world.as_user(p.priya).get(f"{API}/{group['id']}/members").json()["members"]

    assert {m["status"] for m in admin_view} == {"active", "invited"}
    assert {m["status"] for m in member_view} == {"active"}


# --- removing people ---------------------------------------------------------------------------------------------

def test_removing_a_member_ends_their_access_at_once_and_tells_them(world, program):
    p = world.people
    say(world, p.rao, program["id"], "first")
    world.events.clear(), world.notifications.clear()

    resp = world.as_user(p.rao).delete(f"{API}/{program['id']}/members/{p.priya['id']}", params={"reason": "left the program"})

    assert resp.status_code == 200 and resp.json()["status"] == "removed"
    # no access, immediately, on every route
    for method, path in (("get", ""), ("get", "/messages"), ("get", "/members"), ("get", "/pinned"), ("get", "/files")):
        assert getattr(world.as_user(p.priya), method)(f"{API}/{program['id']}{path}").status_code == 403, path
    assert world.as_user(p.priya).post(f"{API}/{program['id']}/messages", json={"content": "hi"}).status_code == 403
    assert world.as_user(p.priya).get(API).json()["items"] == []
    # told: a notification, a realtime event to them, and their sockets leave the room (so the open app stops getting messages)
    assert [n for n in world.notifications if n["category"] == "group_removed" and n["user_ids"] == [uid(p.priya)]]
    assert ("user", p.priya["id"], "member_removed", {"conversation_id": program["id"], "user_id": p.priya["id"]}) in world.events
    assert ("rooms", p.priya["id"], "leave", program["id"]) in world.events
    assert ("group", program["id"], "member_removed", {"user_id": p.priya["id"]}) in world.events  # to the people still in the room


def test_a_removed_person_does_not_get_later_notifications_or_realtime_messages(world, program):
    p = world.people
    world.as_user(p.rao).delete(f"{API}/{program['id']}/members/{p.priya['id']}")
    world.notifications.clear()

    say(world, p.rao, program["id"], "after the removal")

    recipients = {u for n in world.notifications if n["category"] == "group_message" for u in n["user_ids"]}
    assert uid(p.priya) not in recipients and uid(p.ravi) in recipients


def test_their_old_messages_stay_in_the_group(world, program):
    p = world.people
    say(world, p.priya, program["id"], "my question", )  # program groups are providers-only by default...
    world.as_user(p.rao).patch(f"{API}/{program['id']}/settings", json={"admins_only_posting": False})
    say(world, p.priya, program["id"], "my question")

    world.as_user(p.rao).delete(f"{API}/{program['id']}/members/{p.priya['id']}")

    assert "my question" in texts(messages_of(world, p.ravi, program["id"]))


def test_the_owner_cannot_be_removed_by_an_admin(world, care):
    p = world.people
    world.as_user(p.rao).patch(f"{API}/{care['id']}/members/{p.mehta['id']}/role", json={"role": "admin"})

    resp = world.as_user(p.mehta).delete(f"{API}/{care['id']}/members/{p.rao['id']}")

    assert resp.status_code == 403 and "owner cannot be removed" in resp.json()["detail"]
    assert world.as_user(p.rao).get(f"{API}/{care['id']}").status_code == 200


def test_an_admin_cannot_remove_another_admin_but_the_owner_can_with_a_reason(world, program):
    p = world.people
    nair = p.nair
    world.as_user(p.rao).post(f"{API}/{program['id']}/members", json={"invitees": [{"user_id": nair["id"]}]})
    accept(world, nair, program["id"])
    for provider in (p.mehta, nair):
        assert world.as_user(p.rao).patch(f"{API}/{program['id']}/members/{provider['id']}/role", json={"role": "admin"}).status_code == 200

    by_admin = world.as_user(p.mehta).delete(f"{API}/{program['id']}/members/{nair['id']}", params={"reason": "x"})
    no_reason = world.as_user(p.rao).delete(f"{API}/{program['id']}/members/{nair['id']}")
    ok = world.as_user(p.rao).delete(f"{API}/{program['id']}/members/{nair['id']}", params={"reason": "no longer on the team"})

    assert by_admin.status_code == 403 and no_reason.status_code == 422 and ok.status_code == 200
    removed = [e for e in world.as_user(p.rao).get(f"{API}/{program['id']}/audit").json()["items"] if e["action"] == "member_removed"]
    assert removed and removed[0]["reason"] == "no longer on the team"


def test_compliance_can_remove_or_demote_an_admin_with_a_recorded_reason_without_being_in_the_group(world, program):
    """Decision 7: only the owner, or the clinic / platform compliance admin with a recorded reason, can change an admin."""
    p = world.people
    world.as_user(p.rao).patch(f"{API}/{program['id']}/members/{p.mehta['id']}/role", json={"role": "admin"})
    url = f"{API}/{program['id']}/members/{p.mehta['id']}"

    assert world.as_user(p.admin).patch(f"{url}/role", json={"role": "member"}).status_code == 422  # a reason is required
    demoted = world.as_user(p.admin).patch(f"{url}/role", json={"role": "member", "reason": "complaint upheld"})
    assert demoted.status_code == 200 and demoted.json()["role"] == "member"
    world.as_user(p.rao).patch(f"{url}/role", json={"role": "admin"})
    assert world.as_user(p.root).delete(url).status_code == 422
    removed = world.as_user(p.root).delete(url, params={"reason": "registration lapsed"})
    assert removed.status_code == 200
    entries = {e["action"]: e for e in world.as_user(p.root).get(f"{API}/{program['id']}/audit").json()["items"]}
    assert entries["member_removed"]["reason"] == "registration lapsed" and entries["member_removed"]["actor_role"] == "super_admin"


def test_another_clinics_admin_cannot_act_on_this_groups_members(world, program):
    p = world.people

    assert world.as_user(p.outsider_admin).delete(f"{API}/{program['id']}/members/{p.priya['id']}", params={"reason": "x"}).status_code == 403
    assert world.as_user(p.outsider_admin).patch(f"{API}/{program['id']}/members/{p.mehta['id']}/role", json={"role": "admin"}).status_code == 403
    assert world.as_user(p.stranger).delete(f"{API}/{program['id']}/members/{p.priya['id']}").status_code == 403


def test_compliance_still_cannot_remove_the_owner_through_the_member_route(world, program):
    resp = world.as_user(world.people.admin).delete(f"{API}/{program['id']}/members/{world.people.rao['id']}", params={"reason": "x"})

    assert resp.status_code == 403 and "owner cannot be removed" in resp.json()["detail"]  # reassign the group instead


def test_a_member_who_is_not_an_admin_cannot_remove_anyone(world, program):
    resp = world.as_user(world.people.priya).delete(f"{API}/{program['id']}/members/{world.people.ravi['id']}")

    assert resp.status_code == 403


def test_a_pending_invitation_can_be_cancelled(world):
    p = world.people
    group = make_group(world, invitees=[p.priya])

    assert world.as_user(p.rao).delete(f"{API}/{group['id']}/members/{p.priya['id']}").status_code == 200
    assert world.as_user(p.priya).post(f"{API}/{group['id']}/accept", json={"consent": True}).status_code == 404
    assert "invitation_cancelled" in audit_actions(world, group["id"])


def test_removing_someone_who_is_not_in_the_group_is_404(world, program):
    assert world.as_user(world.people.rao).delete(f"{API}/{program['id']}/members/{world.people.stranger['id']}").status_code == 404


def test_you_leave_yourself_rather_than_removing_yourself(world, program):
    assert world.as_user(world.people.rao).delete(f"{API}/{program['id']}/members/{world.people.rao['id']}").status_code == 422


def test_a_removed_person_can_be_invited_again(world, program):
    p = world.people
    world.as_user(p.rao).delete(f"{API}/{program['id']}/members/{p.priya['id']}")
    assert world.as_user(p.rao).post(f"{API}/{program['id']}/members", json={"invitees": [{"user_id": p.priya["id"]}]}).json()["invited"]

    body = accept(world, p.priya, program["id"])

    assert body["my_role"] == "member"
    assert world.as_user(p.priya).get(f"{API}/{program['id']}").status_code == 200


# --- leaving ------------------------------------------------------------------------------------------------------

def test_anyone_can_leave_and_loses_access(world, program):
    p = world.people

    resp = world.as_user(p.priya).post(f"{API}/{program['id']}/leave")

    assert resp.status_code == 200 and resp.json()["status"] == "left"
    assert world.as_user(p.priya).get(f"{API}/{program['id']}/messages").status_code == 403
    assert ("rooms", p.priya["id"], "leave", program["id"]) in world.events
    assert any("left" in t for t in texts(messages_of(world, p.ravi, program["id"])))
    assert "member_left" in audit_actions(world, program["id"])


def test_the_owner_must_hand_the_group_over_before_leaving(world, care):
    resp = world.as_user(world.people.rao).post(f"{API}/{care['id']}/leave")

    assert resp.status_code == 409 and "hand the group over" in resp.json()["detail"]


def test_withdrawing_consent_removes_access_and_keeps_the_record(world, program):
    p = world.people

    resp = world.as_user(p.priya).post(f"{API}/{program['id']}/consent/withdraw")

    assert resp.status_code == 200 and resp.json()["status"] == "withdrawn"
    assert world.as_user(p.priya).get(f"{API}/{program['id']}").status_code == 403
    consent = world.as_user(p.priya).get(f"{API}/consents/me").json()["items"][0]
    assert consent["withdrawn_at"] is not None and consent["conversation_id"] == program["id"]
    assert "consent_withdrawn" in audit_actions(world, program["id"])


def test_withdrawing_consent_before_accepting_is_a_decline(world):
    p = world.people
    group = make_group(world, invitees=[p.priya])

    assert world.as_user(p.priya).post(f"{API}/{group['id']}/consent/withdraw").status_code == 200
    assert world.as_user(p.priya).get(f"{API}/invitations").json()["total"] == 0


def test_the_owner_cannot_withdraw_without_handing_over(world, care):
    resp = world.as_user(world.people.rao).post(f"{API}/{care['id']}/consent/withdraw")

    assert resp.status_code == 409 and resp.json()["detail"] == "The owner must hand the group over before withdrawing"
    mine = world.as_user(world.people.rao).get(f"{API}/{care['id']}").json()
    assert mine["my_role"] == "owner" and mine["permissions"]["can_withdraw_consent"] is False and mine["permissions"]["can_leave"] is False
    assert world.as_user(world.people.rao).get(f"{API}/{care['id']}/members").json()["members"][0]["role"] == "owner"       # still the owner, still a member
    assert world.as_user(world.people.rao).post(f"{API}/{care['id']}/leave").status_code == 409


def test_the_flag_follows_the_ownership_and_withdrawing_works_after_the_handover(world, care):
    p = world.people

    def perms(user):
        return world.as_user(user).get(f"{API}/{care['id']}").json()["permissions"]

    assert perms(p.rao)["can_withdraw_consent"] is False
    assert perms(p.mehta)["can_withdraw_consent"] is True                                                 # a plain member can

    assert world.as_user(p.rao).post(f"{API}/{care['id']}/transfer-ownership", json={"user_id": p.mehta["id"]}).status_code == 200

    assert perms(p.rao)["can_withdraw_consent"] is True
    assert perms(p.mehta)["can_withdraw_consent"] is False                                                # the new owner cannot
    assert world.as_user(p.mehta).post(f"{API}/{care['id']}/consent/withdraw").status_code == 409
    assert world.as_user(p.rao).post(f"{API}/{care['id']}/consent/withdraw").status_code == 200


# --- roles ----------------------------------------------------------------------------------------------------------

def test_a_verified_provider_can_be_made_an_admin(world, care):
    p = world.people

    resp = world.as_user(p.rao).patch(f"{API}/{care['id']}/members/{p.mehta['id']}/role", json={"role": "admin"})

    assert resp.status_code == 200 and resp.json()["role"] == "admin"
    assert world.as_user(p.mehta).get(f"{API}/{care['id']}").json()["my_role"] == "admin"
    assert [n for n in world.notifications if n["category"] == "group_role_changed" and n["user_ids"] == [uid(p.mehta)]]
    assert "role_changed" in audit_actions(world, care["id"])


def test_a_patient_can_never_be_an_admin(world, care):
    resp = world.as_user(world.people.rao).patch(f"{API}/{care['id']}/members/{world.people.priya['id']}/role", json={"role": "admin"})

    assert resp.status_code == 422 and "patients cannot" in resp.json()["detail"]


def test_a_provider_who_is_not_verified_cannot_be_an_admin(world):
    p = world.people
    group = make_group(world, kind="care", invitees=[p.unverified])
    accept(world, p.unverified, group["id"])

    # an unverified provider is treated as an ordinary member (kind patient) until verified
    resp = world.as_user(p.rao).patch(f"{API}/{group['id']}/members/{p.unverified['id']}/role", json={"role": "admin"})

    assert resp.status_code == 422


def test_an_admin_can_make_another_provider_an_admin(world, program):
    p = world.people
    world.as_user(p.rao).post(f"{API}/{program['id']}/members", json={"invitees": [{"user_id": p.nair["id"]}]})
    accept(world, p.nair, program["id"])
    world.as_user(p.rao).patch(f"{API}/{program['id']}/members/{p.mehta['id']}/role", json={"role": "admin"})

    resp = world.as_user(p.mehta).patch(f"{API}/{program['id']}/members/{p.nair['id']}/role", json={"role": "admin"})

    assert resp.status_code == 200


def test_a_plain_member_cannot_change_roles(world, program):
    resp = world.as_user(world.people.priya).patch(f"{API}/{program['id']}/members/{world.people.mehta['id']}/role", json={"role": "admin"})

    assert resp.status_code == 403


def test_at_most_three_admins(world):
    p = world.people
    providers = [p.mehta, p.nair]
    extra = []
    from tests.group_chat_support import person, uid as _uid
    from app.models.group_chat_model import VerifiedProvider
    from tests.group_chat_support import TENANT

    for i in range(2):
        user = person(f"Dr Extra{i}", "provider")
        world.names[user["id"]] = user["name"]
        world.db.add(VerifiedProvider(user_id=_uid(user), tenant_id=TENANT, status="verified"))
        extra.append(user)
    world.db.commit()
    group = joined_group(world, kind="program", members=providers + extra)

    results = [world.as_user(p.rao).patch(f"{API}/{group['id']}/members/{u['id']}/role", json={"role": "admin"}).status_code for u in providers + extra]

    assert results == [200, 200, 200, 409]  # the owner is not counted; the fourth admin is refused


def test_only_the_owner_or_compliance_can_demote_an_admin_and_a_reason_is_needed(world, care):
    p = world.people
    world.as_user(p.rao).patch(f"{API}/{care['id']}/members/{p.mehta['id']}/role", json={"role": "admin"})

    no_reason = world.as_user(p.rao).patch(f"{API}/{care['id']}/members/{p.mehta['id']}/role", json={"role": "member"})
    ok = world.as_user(p.rao).patch(f"{API}/{care['id']}/members/{p.mehta['id']}/role", json={"role": "member", "reason": "rota change"})

    assert no_reason.status_code == 422 and ok.status_code == 200
    entry = [e for e in world.as_user(p.rao).get(f"{API}/{care['id']}/audit").json()["items"] if e["action"] == "role_changed"][0]
    assert entry["reason"] == "rota change" and entry["details"] == {"from": "admin", "to": "member"}


def test_the_owners_role_cannot_be_changed_through_the_role_route(world, care):
    p = world.people
    world.as_user(p.rao).patch(f"{API}/{care['id']}/members/{p.mehta['id']}/role", json={"role": "admin"})

    assert world.as_user(p.mehta).patch(f"{API}/{care['id']}/members/{p.rao['id']}/role", json={"role": "member"}).status_code == 403


# --- ownership --------------------------------------------------------------------------------------------------------

def test_the_owner_hands_the_group_to_another_verified_provider(world, care):
    p = world.people

    resp = world.as_user(p.rao).post(f"{API}/{care['id']}/transfer-ownership", json={"user_id": p.mehta["id"]})

    assert resp.status_code == 200 and resp.json()["owner_id"] == p.mehta["id"]
    assert world.as_user(p.mehta).get(f"{API}/{care['id']}").json()["my_role"] == "owner"
    assert world.as_user(p.rao).get(f"{API}/{care['id']}").json()["my_role"] == "admin"  # the previous owner stays as an admin
    assert world.as_user(p.rao).post(f"{API}/{care['id']}/leave").status_code == 200  # and can now leave
    assert "ownership_transferred" in audit_actions(world, care["id"], p.mehta)


def test_ownership_cannot_go_to_a_patient_or_to_a_non_owner_caller(world, care):
    p = world.people

    assert world.as_user(p.rao).post(f"{API}/{care['id']}/transfer-ownership", json={"user_id": p.priya["id"]}).status_code == 422
    assert world.as_user(p.mehta).post(f"{API}/{care['id']}/transfer-ownership", json={"user_id": p.mehta["id"]}).status_code == 403
    assert world.as_user(p.rao).post(f"{API}/{care['id']}/transfer-ownership", json={"user_id": p.rao["id"]}).status_code == 422
    assert world.as_user(p.rao).post(f"{API}/{care['id']}/transfer-ownership", json={"user_id": p.stranger["id"]}).status_code == 404


def test_compliance_can_reassign_a_group_with_a_recorded_reason(world, care):
    p = world.people

    for who in (p.admin, p.root):
        resp = world.as_user(who).post(f"{API}/{care['id']}/reassign-owner", json={"user_id": p.mehta["id"], "reason": "owner unreachable"})
        assert resp.status_code == 200, resp.text
        world.as_user(p.mehta).post(f"{API}/{care['id']}/transfer-ownership", json={"user_id": p.rao["id"]})  # put it back for the next round

    entries = [e for e in world.as_user(p.root).get(f"{API}/{care['id']}/audit").json()["items"] if e["action"] == "owner_reassigned"]
    assert len(entries) == 2 and entries[0]["reason"] == "owner unreachable"


def test_reassigning_needs_compliance_a_reason_and_a_provider(world, care):
    p = world.people

    assert world.as_user(p.mehta).post(f"{API}/{care['id']}/reassign-owner", json={"user_id": p.mehta["id"], "reason": "because"}).status_code == 403
    assert world.as_user(p.outsider_admin).post(f"{API}/{care['id']}/reassign-owner", json={"user_id": p.mehta["id"], "reason": "because"}).status_code == 403
    assert world.as_user(p.admin).post(f"{API}/{care['id']}/reassign-owner", json={"user_id": p.mehta["id"], "reason": "x"}).status_code == 422
    assert world.as_user(p.admin).post(f"{API}/{care['id']}/reassign-owner", json={"user_id": p.priya["id"], "reason": "because"}).status_code == 422


# --- muting someone ------------------------------------------------------------------------------------------------------

def test_an_admin_can_mute_someone_who_can_still_read_but_not_post(world, program):
    p = world.people
    world.as_user(p.rao).patch(f"{API}/{program['id']}/settings", json={"admins_only_posting": False})
    say(world, p.rao, program["id"], "welcome")

    resp = world.as_user(p.rao).post(f"{API}/{program['id']}/members/{p.priya['id']}/mute-posting", json={"reason": "off topic"})

    assert resp.status_code == 200 and resp.json()["posting_muted"] is True
    assert say(world, p.priya, program["id"], "hello?").status_code == 403
    assert "welcome" in texts(messages_of(world, p.priya, program["id"]))  # still reads
    assert members_by_name(world, p.rao, program["id"])["Priya Sharma"]["posting_muted"] is True
    assert members_by_name(world, p.ravi, program["id"])["Priya S."]["posting_muted"] is False  # others are not shown who is muted
    assert "posting_muted" in audit_actions(world, program["id"])


def test_unmuting_lets_them_post_again(world, program):
    p = world.people
    world.as_user(p.rao).patch(f"{API}/{program['id']}/settings", json={"admins_only_posting": False})
    world.as_user(p.rao).post(f"{API}/{program['id']}/members/{p.priya['id']}/mute-posting", json={})

    resp = world.as_user(p.rao).delete(f"{API}/{program['id']}/members/{p.priya['id']}/mute-posting")

    assert resp.status_code == 200 and resp.json()["posting_muted"] is False
    assert say(world, p.priya, program["id"], "back").status_code == 201
    assert "posting_unmuted" in audit_actions(world, program["id"])


def test_a_timed_mute_ends_by_itself(world, program):
    from app.models.chat_model import ConversationParticipant

    p = world.people
    world.as_user(p.rao).patch(f"{API}/{program['id']}/settings", json={"admins_only_posting": False})
    until = (datetime.utcnow() + timedelta(hours=1)).isoformat()
    assert world.as_user(p.rao).post(f"{API}/{program['id']}/members/{p.priya['id']}/mute-posting", json={"until": until}).status_code == 200
    assert say(world, p.priya, program["id"], "still muted").status_code == 403

    row = world.db.query(ConversationParticipant).filter_by(user_id=uid(p.priya)).one()
    row.posting_muted_until = datetime.utcnow() - timedelta(minutes=1)
    world.db.commit()

    assert say(world, p.priya, program["id"], "mute is over").status_code == 201


def test_a_mute_cannot_end_in_the_past(world, program):
    resp = world.as_user(world.people.rao).post(
        f"{API}/{program['id']}/members/{world.people.priya['id']}/mute-posting", json={"until": (datetime.utcnow() - timedelta(hours=1)).isoformat()})

    assert resp.status_code == 422


def test_the_owner_and_other_admins_cannot_be_muted_by_an_admin(world, care):
    p = world.people
    world.as_user(p.rao).patch(f"{API}/{care['id']}/members/{p.mehta['id']}/role", json={"role": "admin"})

    assert world.as_user(p.mehta).post(f"{API}/{care['id']}/members/{p.rao['id']}/mute-posting", json={}).status_code == 403
    assert world.as_user(p.priya).post(f"{API}/{care['id']}/members/{p.mehta['id']}/mute-posting", json={}).status_code == 403


# --- admins-only posting -------------------------------------------------------------------------------------------------

def test_admins_only_posting_lets_only_providers_post(world, program):
    p = world.people
    assert program["admins_only_posting"] is True

    assert say(world, p.priya, program["id"], "can I ask something?").status_code == 403
    assert say(world, p.mehta, program["id"], "class moved to 7am").status_code == 201  # any provider, not just admins
    assert say(world, p.rao, program["id"], "announcement").status_code == 201


def test_switching_it_off_lets_patients_post_and_is_logged(world, program):
    p = world.people

    resp = world.as_user(p.rao).patch(f"{API}/{program['id']}/settings", json={"admins_only_posting": False})

    assert resp.status_code == 200 and resp.json()["admins_only_posting"] is False
    assert say(world, p.priya, program["id"], "thanks").status_code == 201
    assert "settings_changed" in audit_actions(world, program["id"])
    assert ("group", program["id"], "group_updated", {"admins_only_posting": False}) in world.events


def test_only_admins_can_change_the_posting_setting(world, program):
    assert world.as_user(world.people.priya).patch(f"{API}/{program['id']}/settings", json={"admins_only_posting": False}).status_code == 403


def test_patients_may_be_barred_from_posting_in_program_groups_altogether(world, program, monkeypatch):
    """Open legal question: may patients post to each other in a program group at all? One setting decides."""
    from app.core.config import settings

    p = world.people
    world.as_user(p.rao).patch(f"{API}/{program['id']}/settings", json={"admins_only_posting": False})
    monkeypatch.setattr(settings, "GROUP_PROGRAM_PATIENTS_CAN_POST", False)

    assert say(world, p.priya, program["id"], "hi").status_code == 403
    assert say(world, p.mehta, program["id"], "hi").status_code == 201


# --- closing and reopening --------------------------------------------------------------------------------------------------

def test_closing_makes_the_group_read_only_and_starts_retention(world, program):
    p = world.people
    say(world, p.rao, program["id"], "last message")
    world.notifications.clear()

    resp = world.as_user(p.rao).post(f"{API}/{program['id']}/close", json={"reason": "program ended"})

    body = resp.json()
    assert resp.status_code == 200 and body["status"] == "closed" and body["closed_reason"] == "program ended"
    assert body["retention_started_at"] is not None
    assert datetime.fromisoformat(body["retention_until"]) - datetime.fromisoformat(body["retention_started_at"]) == timedelta(days=3650)
    assert say(world, p.rao, program["id"], "too late").status_code == 409
    assert "last message" in texts(messages_of(world, p.priya, program["id"]))  # members can still read
    assert [n for n in world.notifications if n["category"] == "group_closed"]
    assert "group_closed" in audit_actions(world, program["id"])


def test_a_closed_group_blocks_edits_pins_reactions_and_new_invitations(world, program):
    p = world.people
    sent = say(world, p.mehta, program["id"], "hello").json()
    world.as_user(p.rao).post(f"{API}/{program['id']}/close", json={})

    assert world.as_user(p.mehta).patch(f"{API}/{program['id']}/messages/{sent['id']}", json={"content": "changed"}).status_code == 409
    assert world.as_user(p.rao).post(f"{API}/{program['id']}/members", json={"invitees": [{"user_id": p.stranger["id"]}]}).status_code == 409
    assert world.as_user(p.rao).patch(f"{API}/{program['id']}", json={"description": "new"}).status_code == 409


def test_only_an_admin_closes_and_it_cannot_be_closed_twice(world, program):
    p = world.people

    assert world.as_user(p.priya).post(f"{API}/{program['id']}/close", json={}).status_code == 403
    assert world.as_user(p.rao).post(f"{API}/{program['id']}/close", json={}).status_code == 200
    assert world.as_user(p.rao).post(f"{API}/{program['id']}/close", json={}).status_code == 409


def test_reopening_needs_the_owner_and_a_reason(world, program):
    p = world.people
    world.as_user(p.rao).patch(f"{API}/{program['id']}/members/{p.mehta['id']}/role", json={"role": "admin"})
    world.as_user(p.rao).post(f"{API}/{program['id']}/close", json={})

    assert world.as_user(p.rao).post(f"{API}/{program['id']}/reopen", json={}).status_code == 422  # a reason is required
    assert world.as_user(p.mehta).post(f"{API}/{program['id']}/reopen", json={"reason": "more sessions"}).status_code == 403  # an admin is not enough
    ok = world.as_user(p.rao).post(f"{API}/{program['id']}/reopen", json={"reason": "more sessions"})

    assert ok.status_code == 200 and ok.json()["status"] == "open" and ok.json()["retention_started_at"] is None
    assert say(world, p.rao, program["id"], "we are back").status_code == 201
    entry = [e for e in world.as_user(p.rao).get(f"{API}/{program['id']}/audit").json()["items"] if e["action"] == "group_reopened"][0]
    assert entry["reason"] == "more sessions"


def test_reopening_an_open_group_is_refused(world, program):
    assert world.as_user(world.people.rao).post(f"{API}/{program['id']}/reopen", json={"reason": "why not"}).status_code == 409


# --- a member's own list -----------------------------------------------------------------------------------------------------

def test_muting_a_group_never_removes_access(world, program):
    p = world.people

    resp = world.as_user(p.priya).patch(f"{API}/{program['id']}/preferences", json={"muted": True})

    assert resp.status_code == 200 and resp.json()["muted"] is True
    assert world.as_user(p.priya).get(f"{API}/{program['id']}/messages").status_code == 200
    assert world.as_user(p.priya).get(API).json()["items"][0]["muted"] is True


def test_a_timed_mute_ends_by_itself_for_notifications(world, program):
    from app.models.chat_model import ConversationParticipant

    p = world.people
    world.as_user(p.priya).patch(f"{API}/{program['id']}/preferences", json={"muted": True, "muted_until": (datetime.utcnow() + timedelta(hours=8)).isoformat()})
    row = world.db.query(ConversationParticipant).filter_by(user_id=uid(p.priya)).one()
    row.muted_until = datetime.utcnow() - timedelta(minutes=1)
    world.db.commit()

    assert world.as_user(p.priya).get(f"{API}/{program['id']}").json()["muted"] is False


def test_pinned_groups_come_first_in_my_list(world):
    p = world.people
    first = joined_group(world, kind="program", members=[p.priya], name="Alpha batch")
    second = joined_group(world, kind="program", members=[p.priya], name="Beta batch")

    before = [g["name"] for g in world.as_user(p.priya).get(API).json()["items"]]
    world.as_user(p.priya).patch(f"{API}/{first['id']}/preferences", json={"pinned": True})
    after = [g["name"] for g in world.as_user(p.priya).get(API).json()["items"]]

    assert before[0] == "Beta batch" and after[0] == "Alpha batch"


def test_a_member_can_archive_a_group_for_themselves_only(world, program):
    p = world.people

    assert world.as_user(p.priya).patch(f"/api/v1/conversations/{program['id']}/archive", json={"archived": True}).status_code == 200

    assert world.as_user(p.priya).get(API).json()["items"] == []
    assert [g["name"] for g in world.as_user(p.priya).get(API, params={"archived": True}).json()["items"]] == ["Yoga batch Oct 2026"]
    assert world.as_user(p.ravi).get(API).json()["total"] == 1  # nobody else's list changed
    assert world.as_user(p.priya).get(f"{API}/{program['id']}").status_code == 200  # access is unchanged


def test_the_groups_list_filters_by_kind_and_search(world):
    p = world.people
    joined_group(world, kind="program", members=[p.priya], name="Morning yoga")
    joined_group(world, kind="care", members=[p.priya], name="Recovery team")

    assert [g["name"] for g in world.as_user(p.priya).get(API, params={"kind": "care"}).json()["items"]] == ["Recovery team"]
    assert [g["name"] for g in world.as_user(p.priya).get(API, params={"q": "yoga"}).json()["items"]] == ["Morning yoga"]
    assert world.as_user(p.priya).get(API).json()["total"] == 2
