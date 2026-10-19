"""The "verify providers for group chat" screen: the list with names, the picker of clinic members that can be verified,
adding and revoking. Real routes, with the identity service's member list stubbed."""
from uuid import uuid4

import pytest

from app.models.group_chat_model import VerifiedProvider
from tests.group_chat_support import OTHER_TENANT, TENANT, person, uid, world  # noqa: F401

API = "/api/v1/groups/verified-providers"


def member(user, role="internal_user", status="active", **extra):
    return {"userId": str(uid(user)), "membershipId": str(uuid4()), "role": role, "membershipStatus": status, "name": user["name"], **extra}


@pytest.fixture
def clinic(world, monkeypatch):
    """The identity service's member list of the clinic: three clinicians, an owner, a learner, an inactive clinician."""
    p = world.people
    enzo = person("Dr Enzo", "provider")
    ines = person("Dr Ines", "provider")
    gone = person("Dr Gone", "provider")
    world.names[enzo["id"]] = "Dr Enzo"
    world.names[ines["id"]] = "Dr Ines"
    records = [
        member(enzo, "internal_user"), member(ines, "tenant_admin"), member(p.rao, "internal_user"),
        member(p.admin, "tenant_owner"), member(p.priya, "external_user"), member(gone, "internal_user", status="suspended"),
    ]
    monkeypatch.setattr("app.services.event_notification_service.owning_tenant_users", lambda tenant, token: (list(records), "stub"))
    world.enzo, world.ines, world.records = enzo, ines, records
    return world


def candidates(w, user, **params):
    resp = w.as_user(user).get(f"{API}/candidates", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- the picker --------------------------------------------------------------------------------------------------------

def test_the_picker_lists_active_clinicians_who_are_not_verified_yet(clinic):
    body = candidates(clinic, clinic.people.admin)

    assert body["tenant_id"] == str(TENANT)
    assert [i["name"] for i in body["items"]] == ["Dr Enzo", "Dr Ines"]              # not Dr Rao (verified), the owner, the learner, nor the suspended
    assert body["items"][0] == {"user_id": clinic.enzo["id"], "name": "Dr Enzo", "roles": ["internal_user"], "verification": "not_verified", "registration_number": None}
    assert body["total"] == 2


def test_the_picker_can_show_everyone_and_the_ones_already_verified(clinic):
    everyone = candidates(clinic, clinic.people.admin, all_roles="true", include_verified="true")["items"]
    states = {i["name"]: i["verification"] for i in everyone}

    assert states == {
        "Dr Enzo": "not_verified", "Dr Ines": "not_verified", "Dr Rao": "verified", "Clinic Admin": "not_verified", "Priya Sharma": "not_verified",
    }
    rao = next(i for i in everyone if i["name"] == "Dr Rao")
    assert rao["registration_number"] == "REG-Rao"


def test_a_revoked_provider_is_offered_again_marked_revoked(clinic):
    row = clinic.db.query(VerifiedProvider).filter_by(user_id=uid(clinic.people.rao)).one()
    row.status = "revoked"
    clinic.db.commit()

    body = candidates(clinic, clinic.people.admin)["items"]

    assert next(i for i in body if i["name"] == "Dr Rao")["verification"] == "revoked"
    # verifying again works and takes them out of the picker
    again = clinic.as_user(clinic.people.admin).post(API, json={"user_id": clinic.people.rao["id"], "registration_number": "NEW-1"})
    assert again.status_code == 201 and again.json()["status"] == "verified"
    assert "Dr Rao" not in [i["name"] for i in candidates(clinic, clinic.people.admin)["items"]]


def test_the_picker_searches_and_pages(clinic):
    assert [i["name"] for i in candidates(clinic, clinic.people.admin, q="enz")["items"]] == ["Dr Enzo"]
    page = candidates(clinic, clinic.people.admin, page=2, page_size=1)
    assert [i["name"] for i in page["items"]] == ["Dr Ines"] and page["total"] == 2 and page["page"] == 2


def test_a_member_with_no_name_in_the_record_gets_the_name_from_the_lookup(clinic):
    nameless = person("Dr Quiet", "provider")
    clinic.names[nameless["id"]] = "Dr Quiet"
    clinic.records.append({"userId": str(uid(nameless)), "membershipId": str(uuid4()), "role": "internal_user", "membershipStatus": "active"})

    assert "Dr Quiet" in [i["name"] for i in candidates(clinic, clinic.people.admin)["items"]]


def test_only_the_clinics_admin_or_the_super_admin_can_open_the_picker(clinic):
    for user in (clinic.people.rao, clinic.people.priya):
        assert clinic.as_user(user).get(f"{API}/candidates").status_code == 403
    assert clinic.as_user(clinic.people.outsider_admin).get(f"{API}/candidates", params={"tenant_id": str(TENANT)}).status_code == 403
    assert clinic.as_user(clinic.people.admin).get(f"{API}/candidates", params={"tenant_id": str(OTHER_TENANT)}).status_code == 403


def test_a_super_admin_names_the_clinic(clinic):
    assert clinic.as_user(clinic.people.root).get(f"{API}/candidates").status_code == 422
    body = candidates(clinic, clinic.people.root, tenant_id=str(TENANT))
    assert body["total"] == 2 and body["tenant_id"] == str(TENANT)


# --- the list, adding and revoking ---------------------------------------------------------------------------------

def test_the_list_carries_names(clinic):
    items = clinic.as_user(clinic.people.admin).get(API).json()

    assert {i["name"] for i in items} == {"Dr Rao", "Dr Mehta", "Dr Nair"} and all(i["status"] == "verified" for i in items)


def test_the_whole_screen_flow_pick_add_see_revoke(clinic):
    w, admin = clinic, clinic.people.admin
    pick = candidates(w, admin)["items"][0]

    added = w.as_user(admin).post(API, json={"user_id": pick["user_id"], "registration_number": "MH-12345", "registration_council": "State Medical Council"})
    assert added.status_code == 201, added.text
    assert added.json()["name"] == "Dr Enzo" and added.json()["status"] == "verified" and added.json()["registration_number"] == "MH-12345"
    assert pick["user_id"] not in [i["user_id"] for i in candidates(w, admin)["items"]]          # no longer offered
    assert next(i for i in w.as_user(admin).get(API).json() if i["user_id"] == pick["user_id"])["name"] == "Dr Enzo"

    # the provider can create a group straight away
    created = w.as_user(w.enzo).post("/api/v1/groups", json={"name": "Enzo batch", "kind": "program", "context_type": "program", "context_id": str(uuid4())})
    assert created.status_code == 201, created.text

    revoked = w.as_user(admin).delete(f"{API}/{pick['user_id']}")
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked" and revoked.json()["name"] == "Dr Enzo"
    again = w.as_user(w.enzo).post("/api/v1/groups", json={"name": "Another", "kind": "program", "context_type": "program", "context_id": str(uuid4())})
    assert again.status_code == 403


def test_a_provider_cannot_verify_themselves(clinic):
    resp = clinic.as_user(clinic.enzo).post(API, json={"user_id": clinic.enzo["id"]})

    assert resp.status_code == 403


# --- only active members of the clinic can be verified -------------------------------------------------------------------

def verify(w, caller, user, **extra):
    return w.as_user(caller).post(API, json={"user_id": user["id"], **extra})


@pytest.mark.parametrize("role", ["tenant_owner", "tenant_admin", "enterprise_admin", "internal_user", "staff", "provider"])
def test_an_active_member_with_an_eligible_role_can_be_verified(world, role):
    colleague = person("Dr Colleague", "provider")
    world.clinic_members.append(world.member_record(colleague, role))

    resp = verify(world, world.people.admin, colleague, registration_number="MH-1")

    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "verified" and resp.json()["tenant_id"] == str(TENANT)


def test_an_enterprise_owner_known_by_role_list_only_can_be_verified(world):
    """The identity service may give the role only in tenantRbacRoles; role: tenant_owner is the usual case."""
    owner = person("Clinic Owner", "admin")
    world.clinic_members.append({"userId": owner["id"], "membershipId": str(uuid4()), "role": "tenant_owner",
                                 "tenantRbacRoles": ["enterprise_admin", "tenant_owner"], "membershipStatus": "active", "userStatus": "active"})

    assert verify(world, world.people.admin, owner).status_code == 201


def test_someone_outside_the_clinic_cannot_be_verified(world):
    stranger = person("Dr Elsewhere", "provider", tenant=OTHER_TENANT)

    resp = verify(world, world.people.admin, stranger)

    assert resp.status_code == 422 and resp.json()["detail"] == "The user is not a member of this clinic"
    assert world.db.query(VerifiedProvider).filter_by(user_id=uid(stranger)).count() == 0


def test_a_user_id_that_exists_nowhere_is_refused_too(world):
    assert world.as_user(world.people.admin).post(API, json={"user_id": str(uuid4())}).status_code == 422


def test_an_inactive_membership_cannot_be_verified(world):
    gone = person("Dr Gone", "provider")
    world.clinic_members.append(world.member_record(gone, "internal_user", status="suspended"))
    paused = person("Dr Paused", "provider")
    world.clinic_members.append({**world.member_record(paused, "internal_user"), "userStatus": "disabled"})

    for user in (gone, paused):
        resp = verify(world, world.people.admin, user)
        assert resp.status_code == 422 and resp.json()["detail"] == "The user's membership in this clinic is not active"


def test_a_learner_cannot_be_verified_as_a_provider(world):
    resp = verify(world, world.people.admin, world.people.priya)

    assert resp.status_code == 422 and "role" in resp.json()["detail"]


def test_a_revoked_provider_who_left_the_clinic_cannot_be_verified_again(world):
    p = world.people
    world.clinic_members[:] = [m for m in world.clinic_members if m["userId"] != p.rao["id"]]
    row = world.db.query(VerifiedProvider).filter_by(user_id=uid(p.rao)).one()
    row.status = "revoked"
    world.db.commit()

    assert verify(world, p.admin, p.rao).status_code == 422
    assert world.db.query(VerifiedProvider).filter_by(user_id=uid(p.rao)).one().status == "revoked"


def test_the_super_admin_is_checked_against_the_clinic_they_name(world):
    p = world.people

    assert verify(world, p.root, p.unverified, tenant_id=str(TENANT)).status_code == 201
    outside = verify(world, p.root, p.nair, tenant_id=str(OTHER_TENANT))
    assert outside.status_code == 422 and outside.json()["detail"] == "The user is not a member of this clinic"


def test_the_super_admin_must_name_the_clinic_for_a_new_provider(world):
    resp = verify(world, world.people.root, world.people.unverified)

    assert resp.status_code == 422 and resp.json()["detail"] == "tenant_id is required"


def test_when_the_member_list_cannot_be_read_nothing_is_verified(world, monkeypatch):
    monkeypatch.setattr("app.services.event_notification_service.owning_tenant_users", lambda tenant, token: ([], "stub"))

    resp = verify(world, world.people.admin, world.people.unverified)

    assert resp.status_code == 503
    assert world.db.query(VerifiedProvider).filter_by(user_id=uid(world.people.unverified)).count() == 0


def test_other_callers_are_still_refused_before_any_lookup(world, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the member list must not be read for a caller who may not verify")

    monkeypatch.setattr("app.services.event_notification_service.owning_tenant_users", boom)
    p = world.people

    assert verify(world, p.rao, p.unverified).status_code == 403
    assert verify(world, p.outsider_admin, p.unverified, tenant_id=str(TENANT)).status_code == 403
