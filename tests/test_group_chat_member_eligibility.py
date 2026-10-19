"""GET /groups/{id}/members tells the owner and admins who can be made an admin or handed the group, so the app does not need
the verified-provider list. The fields must always agree with what PATCH .../role and POST .../transfer-ownership accept."""
import pytest

from app.core.config import settings
from app.models.group_chat_model import VerifiedProvider
from tests.group_chat_support import TENANT, accept, make_group, uid, world  # noqa: F401

API = "/api/v1/groups"


def members(w, viewer, group_id):
    resp = w.as_user(viewer).get(f"{API}/{group_id}/members")
    assert resp.status_code == 200, resp.text
    return {m["user_id"]: m for m in resp.json()["members"]}


@pytest.fixture
def group(world):
    """Dr Rao owns a group with Dr Mehta and Dr Nair (verified providers), Dr Unverified and two patients, all joined."""
    p = world.people
    g = make_group(world, p.rao, "program", invitees=[p.mehta, p.nair, p.unverified, p.priya, p.ravi])
    for user in (p.mehta, p.nair, p.unverified, p.priya, p.ravi):
        accept(world, user, g["id"])
    world.group = g
    return world


def promote(w, owner, member):
    return w.as_user(owner).patch(f"{API}/{w.group['id']}/members/{member['id']}/role", json={"role": "admin"})


def transfer(w, owner, member):
    return w.as_user(owner).post(f"{API}/{w.group['id']}/transfer-ownership", json={"user_id": member["id"]})


def test_the_owner_sees_who_is_eligible_without_the_verified_provider_list(group):
    p = group.people
    seen = members(group, p.rao, group.group["id"])

    for provider in (p.mehta, p.nair):
        assert seen[provider["id"]]["is_verified_provider"] is True
        assert seen[provider["id"]]["can_be_admin"] is True and seen[provider["id"]]["can_receive_ownership"] is True
    for patient in (p.priya, p.ravi):
        assert seen[patient["id"]]["is_verified_provider"] is False
        assert seen[patient["id"]]["can_be_admin"] is False and seen[patient["id"]]["can_receive_ownership"] is False
    owner = seen[p.rao["id"]]
    assert owner["is_verified_provider"] is True and owner["can_be_admin"] is False and owner["can_receive_ownership"] is False


def test_a_provider_who_is_not_verified_is_not_eligible(group):
    p = group.people
    seen = members(group, p.rao, group.group["id"])[p.unverified["id"]]

    assert seen["kind"] == "patient"                                   # an unverified provider joins as a patient
    assert seen["is_verified_provider"] is False and seen["can_be_admin"] is False and seen["can_receive_ownership"] is False


def test_a_new_owner_gets_the_fields_too_and_a_plain_provider_member_does_not(group):
    p = group.people
    assert transfer(group, p.rao, p.mehta).status_code == 200                    # Enzo-style: a verified provider becomes owner

    as_new_owner = members(group, p.mehta, group.group["id"])
    assert as_new_owner[p.nair["id"]]["can_be_admin"] is True and as_new_owner[p.rao["id"]]["can_receive_ownership"] is True
    assert as_new_owner[p.mehta["id"]]["can_receive_ownership"] is False         # not themselves
    as_member = members(group, p.nair, group.group["id"])
    assert all(m["can_be_admin"] is None and m["is_verified_provider"] is None and m["can_receive_ownership"] is None for m in as_member.values())
    as_patient = members(group, p.priya, group.group["id"])
    assert all(m["can_be_admin"] is None for m in as_patient.values())


def test_an_admin_sees_the_fields_and_an_admin_is_not_offered_again(group):
    p = group.people
    assert promote(group, p.rao, p.mehta).status_code == 200

    seen = members(group, p.mehta, group.group["id"])

    assert seen[p.mehta["id"]]["role"] == "admin" and seen[p.mehta["id"]]["can_be_admin"] is False
    assert seen[p.nair["id"]]["can_be_admin"] is True and seen[p.rao["id"]]["can_be_admin"] is False


def test_the_admin_limit_turns_can_be_admin_off_for_everyone(group, monkeypatch):
    p = group.people
    monkeypatch.setattr(settings, "GROUP_MAX_ADMINS", 1)
    assert promote(group, p.rao, p.mehta).status_code == 200

    seen = members(group, p.rao, group.group["id"])

    assert seen[p.nair["id"]]["can_be_admin"] is False and seen[p.nair["id"]]["is_verified_provider"] is True
    assert seen[p.nair["id"]]["can_receive_ownership"] is True                   # the limit does not stop a transfer
    assert promote(group, p.rao, p.nair).status_code == 409


def test_a_verification_revoked_after_joining_shows_as_not_eligible(group):
    p = group.people
    row = group.db.query(VerifiedProvider).filter_by(user_id=uid(p.nair)).one()
    row.status = "revoked"
    group.db.commit()

    seen = members(group, p.rao, group.group["id"])[p.nair["id"]]

    assert seen["kind"] == "provider" and seen["is_verified_provider"] is False
    assert seen["can_be_admin"] is False and seen["can_receive_ownership"] is False
    assert promote(group, p.rao, p.nair).status_code == 422
    assert transfer(group, p.rao, p.nair).status_code == 422


def test_a_pending_invitation_is_not_eligible(group):
    p = group.people
    outsider = group.people.asha
    group.as_user(p.rao).post(f"{API}/{group.group['id']}/members", json={"invitees": [{"user_id": outsider["id"]}]})

    seen = members(group, p.rao, group.group["id"])[outsider["id"]]

    assert seen["status"] == "invited" and seen["can_be_admin"] is False and seen["can_receive_ownership"] is False


def test_the_fields_always_agree_with_what_the_endpoints_accept(group):
    """For every member: can_be_admin true <=> the promotion is accepted; can_receive_ownership true <=> the transfer is."""
    p = group.people
    seen = members(group, p.rao, group.group["id"])
    for user in (p.mehta, p.nair, p.unverified, p.priya, p.ravi):
        assert (promote(group, p.rao, user).status_code == 200) == seen[user["id"]]["can_be_admin"], user["name"]
        # undo, so the next check starts from the same group
        group.as_user(p.rao).patch(f"{API}/{group.group['id']}/members/{user['id']}/role", json={"role": "member", "reason": "test reset"})
    for user in (p.unverified, p.priya, p.ravi, p.nair):
        assert (transfer(group, p.rao, user).status_code == 200) == seen[user["id"]]["can_receive_ownership"], user["name"]
        if seen[user["id"]]["can_receive_ownership"]:
            break                                                               # the group has a new owner now


def test_the_verified_provider_list_stays_closed_to_a_group_owner(group):
    assert group.as_user(group.people.rao).get(f"{API}/verified-providers").status_code == 403
