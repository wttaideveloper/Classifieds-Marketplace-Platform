"""Names in group chat when the identity service cannot name someone: the owner in the members list, the inviter and the
providers on an invitation. The name each person's own sign-in carries is kept (on their group membership and in the
saved names), and never an email address. The admin who owns a group is still listed as a provider: the group has no
other kind for someone who can act as one."""
from uuid import UUID, uuid4

import pytest

from app.models.chat_model import ConversationParticipant, UserDisplayName
from app.services import chat_user_names
from tests.group_chat_support import person, uid, world  # noqa: F401

API = "/api/v1/groups"
REAL_RESOLVER = chat_user_names.resolve_display_names       # the fixture replaces it with a lookup table; these tests want the real one


@pytest.fixture
def silent_identity(world, monkeypatch):
    """The real name resolver, with an identity service that knows nobody."""
    UserDisplayName.__table__.create(world.engine, checkfirst=True)
    monkeypatch.setattr(chat_user_names, "resolve_display_names", REAL_RESOLVER)
    monkeypatch.setattr(chat_user_names.identity, "list_tenant_users", lambda tenant_id, access_token=None: [])
    monkeypatch.setattr(chat_user_names.identity, "list_tenant_members", lambda access_token=None: [])
    monkeypatch.setattr(chat_user_names.identity, "fetch_internal_user_by_id", lambda user_id: None)
    chat_user_names.clear_cache()
    yield world
    chat_user_names.clear_cache()


def new_group(w, owner, invitees=()):
    resp = w.as_user(owner).post(API, json={
        "name": "Yoga batch", "kind": "program", "context_type": "program", "context_id": str(uuid4()),
        "invitees": [{"user_id": u["id"]} for u in invitees],
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def members(w, viewer, group_id):
    resp = w.as_user(viewer).get(f"{API}/{group_id}/members")
    assert resp.status_code == 200, resp.text
    return {m["user_id"]: m for m in resp.json()["members"]}


def test_the_enterprise_admin_owner_is_named_even_when_the_identity_lookup_has_nothing(silent_identity):
    w, p = silent_identity, silent_identity.people
    group = new_group(w, p.admin, [p.rao])
    w.as_user(p.rao).post(f"{API}/{group['id']}/accept", json={"consent": True})

    seen_by_rao = members(w, p.rao, group["id"])

    assert seen_by_rao[p.admin["id"]]["name"] == "Clinic Admin"
    assert seen_by_rao[p.admin["id"]]["role"] == "owner"


def test_the_admin_owner_is_still_a_provider_in_the_group(silent_identity):
    w, p = silent_identity, silent_identity.people
    group = new_group(w, p.admin)

    owner = members(w, p.admin, group["id"])[p.admin["id"]]

    assert owner["kind"] == "provider" and owner["role"] == "owner"        # intentional: the group has only provider and patient


def test_the_invitation_names_the_inviter_and_the_providers(silent_identity):
    w, p = silent_identity, silent_identity.people
    group = new_group(w, p.admin, [p.priya])

    invitation = w.as_user(p.priya).get(f"{API}/invitations/{group['id']}").json()

    assert invitation["invited_by_name"] == "Clinic Admin"
    assert invitation["provider_names"] == ["Clinic Admin"]
    listed = w.as_user(p.priya).get(f"{API}/invitations").json()["items"][0]
    assert listed["invited_by_name"] == "Clinic Admin" and listed["provider_names"] == ["Clinic Admin"]


def test_a_provider_who_accepts_is_named_for_everyone(silent_identity):
    w, p = silent_identity, silent_identity.people
    group = new_group(w, p.admin, [p.rao, p.priya])
    w.as_user(p.rao).post(f"{API}/{group['id']}/accept", json={"consent": True})
    w.as_user(p.priya).post(f"{API}/{group['id']}/accept", json={"consent": True})

    seen_by_priya = members(w, p.priya, group["id"])

    assert seen_by_priya[p.rao["id"]]["name"] == "Dr Rao" and seen_by_priya[p.admin["id"]]["name"] == "Clinic Admin"
    row = w.db.query(ConversationParticipant).filter_by(conversation_id=UUID(group["id"]), user_id=uid(p.rao)).one()
    assert row.display_name == "Dr Rao"


def test_a_group_made_before_the_fix_gets_the_ownersname_from_the_saved_names(silent_identity):
    w, p = silent_identity, silent_identity.people
    group = new_group(w, p.admin, [p.rao])
    w.as_user(p.rao).post(f"{API}/{group['id']}/accept", json={"consent": True})
    owner = w.db.query(ConversationParticipant).filter_by(conversation_id=UUID(group["id"]), user_id=uid(p.admin)).one()
    owner.display_name = None                                                # as in a group created by the old code
    w.db.query(UserDisplayName).delete()
    w.db.commit()
    chat_user_names.clear_cache()
    assert members(w, p.rao, group["id"])[p.admin["id"]]["name"] is None     # nothing known yet

    w.as_user(p.admin).get(f"{API}/{group['id']}")                           # the admin opens their group once

    chat_user_names.clear_cache()
    assert members(w, p.rao, group["id"])[p.admin["id"]]["name"] == "Clinic Admin"


def test_an_email_in_the_name_claim_is_never_used(silent_identity):
    w, p = silent_identity, silent_identity.people
    emailish = {**p.admin, "name": "admin@example.test"}
    group = new_group(w, emailish, [p.rao])
    w.as_user(p.rao).post(f"{API}/{group['id']}/accept", json={"consent": True})

    seen = members(w, p.rao, group["id"])

    assert seen[p.admin["id"]]["name"] is None
    assert w.db.query(UserDisplayName).count() == 0 or all("@" not in r.display_name for r in w.db.query(UserDisplayName).all())
    invitation_owner = w.db.query(ConversationParticipant).filter_by(conversation_id=UUID(group["id"]), user_id=uid(p.admin)).one()
    assert invitation_owner.display_name is None


def test_the_identity_service_name_still_wins_when_it_has_one(silent_identity, monkeypatch):
    w, p = silent_identity, silent_identity.people
    group = new_group(w, p.admin, [p.rao])
    w.as_user(p.rao).post(f"{API}/{group['id']}/accept", json={"consent": True})
    monkeypatch.setattr(
        chat_user_names.identity, "list_tenant_users",
        lambda tenant_id, access_token=None: [{"userId": p.admin["id"], "membershipId": "m-1", "name": "Enterprise Admin Account"}],
    )
    chat_user_names.clear_cache()

    assert members(w, p.rao, group["id"])[p.admin["id"]]["name"] == "Enterprise Admin Account"
