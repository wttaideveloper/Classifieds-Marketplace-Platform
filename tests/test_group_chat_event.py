"""Group chat for Events: pick an event, pick the registrants, create the group, invite more later. Real routes on an
in-memory database.

Covers: the `event` link type, who may manage an event group (the owning enterprise's Enterprise Admin only), the registrant
list and its eligibility rules (online, confirmed or attended, paid or free, one per person, walk-ins never), user ids on
registrations (saved at registration, linked, recovered by email), invitations by id and "everyone eligible", the 50-member
limit, several groups for one event, eligibility checked again on later invitations and on accepting, enterprise isolation,
manual close / reopen, and that Provider members never become admins or owners.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models.enterprise_model import Enterprise
from app.models.event_aux_models import EventOrder, EventRegistration, EventRegistrationOption, EventWaitlist
from app.models.event_model import Event
from tests.group_chat_support import OTHER_TENANT, TENANT, accept, person, say, uid, world  # noqa: F401

API = "/api/v1/groups"
OFFLINE = (None, False)


@pytest.fixture
def ew(world, monkeypatch):
    """The `world` of the group tests plus an enterprise's events and registrations."""
    for model in (Enterprise, Event, EventRegistration, EventOrder, EventRegistrationOption, EventWaitlist):
        model.__table__.create(world.engine, checkfirst=True)
    db, p = world.db, world.people

    ent = Enterprise(tenant_id=TENANT, business_short_name="Clinic", business_legal_name="Clinic Ltd", business_email="c@example.test", status="approved")
    other_ent = Enterprise(tenant_id=OTHER_TENANT, business_short_name="Other", business_legal_name="Other Ltd", business_email="o@example.test", status="approved")
    db.add_all([ent, other_ent])
    db.commit()

    def event(title, enterprise=ent, tenant=TENANT, status="published", **kw):
        e = Event(id=uuid4(), enterprise_id=enterprise.id, tenant_id=tenant, title=title, status=status, **kw)
        db.add(e)
        db.commit()
        return e

    free = event("Wellness Day", start_date=datetime.utcnow() + timedelta(days=10))
    paid = event("Health Summit", pricing_type="paid", price="500", currency="INR",
                 ticket_types=[{"id": "general", "name": "General", "price": "500"}, {"id": "pass", "name": "Free pass", "price": "0"}])
    draft = event("Draft Event", status="draft")
    done = event("Past Camp", status="completed")
    foreign = event("Foreign Event", enterprise=other_ent, tenant=OTHER_TENANT)

    def reg(e, user=None, name=None, email=None, status="confirmed", payment=None, source=None, ticket=None, user_id="auto", age=0):
        user_id = (uid(user) if user else None) if user_id == "auto" else user_id
        created = datetime.utcnow() - timedelta(minutes=age)
        row = EventRegistration(
            event_id=e.id, participant_name=name or (user["name"] if user else "Walk In"),
            participant_email=email or (user["email"] if user else f"{uuid4().hex[:6]}@example.test"), status=status,
            user_id=user_id, registration_source=source, ticket_type_id=ticket, created_at=created, qr_code=uuid4().hex[:12],
        )
        db.add(row)
        if payment:   # the order that paid for it, created just before the registration
            status_, pay = {"paid": ("confirmed", "confirmed"), "pending": ("pending", "pending"), "refunded": ("refunded", "refunded"),
                            "refund_requested": ("refund_requested", "confirmed"), "failed": ("confirmed", "failed"),
                            "cancelled": ("cancelled", "confirmed")}[payment]
            db.add(EventOrder(event_id=e.id, participant_name=row.participant_name, participant_email=row.participant_email,
                              ticket_type_id=ticket, amount="500", status=status_, payment_status=pay, created_at=created - timedelta(minutes=1)))
        db.commit()
        return row

    members = []   # what the identity service's member list says
    monkeypatch.setattr("app.services.event_notification_service.list_tenant_users", lambda tenant, access_token=None: list(members))
    monkeypatch.setattr("app.services.invigorate_auth_client.fetch_profile_email", lambda token: OFFLINE)

    world.event = SimpleNamespace(free=free, paid=paid, draft=draft, done=done, foreign=foreign, reg=reg, members=members, db=db, ent=ent)
    return world


def create(w, user, event, invitees=(), **body):
    payload = {
        "name": "Wellness Day Chat", "kind": "program", "context_type": "event", "context_id": str(event.id),
        "invitees": [{"user_id": u["id"]} if isinstance(u, dict) else {"user_id": str(u)} for u in invitees], **body,
    }
    return w.as_user(user).post(API, json=payload)


def listing(w, user, event, **params):
    resp = w.as_user(user).get(f"{API}/events/{event.id}/attendees", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def by_name(body):
    return {i["name"]: i for i in body["items"]}


def members_of(w, user, group_id):
    resp = w.as_user(user).get(f"{API}/{group_id}/members")
    assert resp.status_code == 200, resp.text
    return {m["user_id"]: m for m in resp.json()["members"]}


# =============================================================================================================
# 1. the link type and who may manage an event group
# =============================================================================================================

def test_an_event_can_be_the_link_of_a_group(ew):
    e, p = ew.event, ew.people
    e.reg(e.free, p.priya)

    resp = create(ew, p.admin, e.free, [p.priya])

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["context_type"] == "event" and body["context_id"] == str(e.free.id) and body["context_name"] == "Wellness Day"
    assert body["kind"] == "program" and body["my_role"] == "owner" and body["limits"]["max_members"] == 50


def test_an_event_group_is_a_program_group(ew):
    resp = create(ew, ew.people.admin, ew.event.free, kind="care")

    assert resp.status_code == 422 and "program" in resp.text


def test_the_invite_everyone_flags_belong_to_their_own_link_type(ew):
    assert create(ew, ew.people.admin, ew.event.free, invite_all_enrolled=True).status_code == 422
    resp = ew.as_user(ew.people.admin).post(API, json={
        "name": "X", "kind": "program", "context_type": "program", "context_id": str(uuid4()), "invite_all_registered": True})
    assert resp.status_code == 422


def test_an_unknown_event_is_refused_like_any_other_link(ew):
    resp = ew.as_user(ew.people.admin).post(API, json={"name": "X", "kind": "program", "context_type": "event", "context_id": str(uuid4())})

    assert resp.status_code == 422 and resp.json()["detail"] == "The linked event was not found"


def test_only_the_enterprise_admin_of_the_owning_enterprise_can_use_an_event(ew):
    e, p = ew.event, ew.people

    assert create(ew, p.admin, e.free).status_code == 201
    for user in (p.rao, p.mehta, p.unverified, p.priya, p.root):          # a Provider (even verified), an attendee, the Super Admin
        assert create(ew, user, e.free).status_code == 403, user["name"]
        assert ew.as_user(user).get(f"{API}/events").status_code == 403, user["name"]
        assert ew.as_user(user).get(f"{API}/events/{e.free.id}/attendees").status_code == 403, user["name"]
        assert ew.as_user(user).get(f"{API}/events/{e.free.id}/providers").status_code == 403, user["name"]
        assert ew.as_user(user).get(f"{API}/events/{e.free.id}/groups").status_code == 403, user["name"]


def test_an_event_of_another_enterprise_is_refused(ew):
    e, p = ew.event, ew.people

    assert create(ew, p.admin, e.foreign).status_code == 403
    assert create(ew, p.outsider_admin, e.free).status_code == 403
    for path in ("attendees", "providers", "groups"):
        assert ew.as_user(p.outsider_admin).get(f"{API}/events/{e.free.id}/{path}").status_code == 403, path
        assert ew.as_user(p.admin).get(f"{API}/events/{e.foreign.id}/{path}").status_code == 403, path


def test_a_group_can_be_created_for_a_published_or_completed_event_only(ew):
    e, p = ew.event, ew.people

    assert create(ew, p.admin, e.done).status_code == 201
    resp = create(ew, p.admin, e.draft)
    assert resp.status_code == 409 and "published or completed" in resp.json()["detail"]


# =============================================================================================================
# 2. the events to choose from
# =============================================================================================================

def test_the_admin_sees_the_enterprises_events_with_counts_and_nobody_elses(ew):
    e, p = ew.event, ew.people
    e.reg(e.free, p.priya)
    e.reg(e.free, p.ravi, status="attended")
    e.reg(e.free, p.asha, status="cancelled")
    e.reg(e.free, name="At The Door", source="walk_in")
    create(ew, p.admin, e.free)

    body = ew.as_user(p.admin).get(f"{API}/events").json()

    items = {i["title"]: i for i in body["items"]}
    assert "Foreign Event" not in items and body["total"] == 4
    assert items["Wellness Day"]["registered_count"] == 2 and items["Wellness Day"]["group_count"] == 1
    assert items["Wellness Day"]["can_create_group"] is True and items["Wellness Day"]["reason"] is None
    assert items["Draft Event"]["can_create_group"] is False and items["Draft Event"]["reason"] == "event_not_open"
    only_draft = ew.as_user(p.admin).get(f"{API}/events", params={"status": "draft"}).json()
    assert [i["title"] for i in only_draft["items"]] == ["Draft Event"]
    assert [i["title"] for i in ew.as_user(p.admin).get(f"{API}/events", params={"q": "summit"}).json()["items"]] == ["Health Summit"]


# =============================================================================================================
# 3. the registrants: who can be invited, and why not
# =============================================================================================================

def test_the_list_has_the_platform_user_id_to_invite_and_leaves_out_walk_ins(ew):
    e, p = ew.event, ew.people
    e.reg(e.free, p.priya, age=40)
    e.reg(e.free, p.ravi, status="attended", age=30)
    e.reg(e.free, p.stranger, status="cancelled", age=20)
    e.reg(e.free, name="No Show", email="noshow@example.test", status="no_show", user_id=uuid4(), age=10)
    e.reg(e.free, name="At The Door", source="walk_in", user_id=uuid4())

    body = listing(ew, p.admin, e.free)

    items = by_name(body)
    assert set(items) == {"Priya Sharma", "Ravi Kumar"} and body["total"] == 2 and body["eligible_total"] == 2
    assert items["Priya Sharma"]["user_id"] == p.priya["id"] and items["Priya Sharma"]["can_invite"] is True and items["Priya Sharma"]["reason"] is None
    assert items["Priya Sharma"]["payment_status"] == "free" and items["Ravi Kumar"]["registration_status"] == "attended"
    assert body["walk_in_excluded"] == 1 and body["max_members"] == 50 and body["event_title"] == "Wellness Day"
    # even asking for everything never shows a walk-in
    everything = listing(ew, p.admin, e.free, eligible_only="false")
    assert "At The Door" not in by_name(everything) and everything["walk_in_excluded"] == 1


def test_cancelled_and_no_show_registrations_are_shown_with_a_reason_when_asked_for(ew):
    e, p = ew.event, ew.people
    e.reg(e.free, p.priya)
    e.reg(e.free, p.stranger, status="cancelled")
    e.reg(e.free, name="No Show", email="noshow@example.test", status="no_show", user_id=uuid4())

    items = by_name(listing(ew, p.admin, e.free, eligible_only="false"))

    assert items["Stranger Lee"]["can_invite"] is False and items["Stranger Lee"]["reason"] == "registration_cancelled"
    assert items["No Show"]["can_invite"] is False and items["No Show"]["reason"] == "registration_no_show"
    only = by_name(listing(ew, p.admin, e.free, registration_status="cancelled"))
    assert set(only) == {"Stranger Lee"}


def test_payment_decides_who_can_be_invited_to_a_paid_event(ew):
    e, p = ew.event, ew.people
    people = {n: person(n, "customer") for n in ("Paid", "Unpaid", "Pending", "Refunded", "Refund Requested", "Failed", "Order Cancelled", "Free Pass")}
    for name, user in people.items():
        ew.people.__dict__[name] = user
    e.reg(e.paid, people["Paid"], payment="paid", ticket="general")
    e.reg(e.paid, people["Unpaid"], ticket="general")                                   # no order at all
    e.reg(e.paid, people["Pending"], payment="pending", ticket="general")
    e.reg(e.paid, people["Refunded"], payment="refunded", ticket="general")
    e.reg(e.paid, people["Refund Requested"], payment="refund_requested", ticket="general")
    e.reg(e.paid, people["Failed"], payment="failed", ticket="general")
    e.reg(e.paid, people["Order Cancelled"], payment="cancelled", ticket="general")
    e.reg(e.paid, people["Free Pass"], ticket="pass")                                    # a free ticket of a paid event: nothing to pay

    everything = by_name(listing(ew, p.admin, e.paid, eligible_only="false"))

    assert {n for n, i in everything.items() if i["can_invite"]} == {"Paid", "Free Pass"}
    assert everything["Paid"]["payment_status"] == "paid" and everything["Free Pass"]["payment_status"] == "free"
    assert {n: i["reason"] for n, i in everything.items() if not i["can_invite"]} == {
        "Unpaid": "payment_unpaid", "Pending": "payment_pending", "Refunded": "payment_refunded",
        "Refund Requested": "payment_refund_requested", "Failed": "payment_failed", "Order Cancelled": "payment_cancelled",
    }
    assert set(by_name(listing(ew, p.admin, e.paid))) == {"Paid", "Free Pass"}          # the default list is the eligible ones
    assert set(by_name(listing(ew, p.admin, e.paid, payment_status="refunded"))) == {"Refunded"}


def test_a_registrant_without_an_account_is_listed_but_cannot_be_invited(ew):
    e, p = ew.event, ew.people
    e.reg(e.free, name="No Account", email="noaccount@example.test", user_id=None)

    item = by_name(listing(ew, p.admin, e.free))["No Account"]

    assert item["user_id"] is None and item["can_invite"] is False and item["reason"] == "no_account"


def test_a_missing_user_id_is_recovered_by_email_and_saved(ew):
    e, p = ew.event, ew.people
    row = e.reg(e.free, p.asha, user_id=None)                         # registered by name and email: no user id on the row
    e.members.append({"userId": str(uid(p.asha)), "email": p.asha["email"]})

    item = by_name(listing(ew, p.admin, e.free))["Asha Iyer"]

    assert item["user_id"] == p.asha["id"] and item["can_invite"] is True
    ew.db.refresh(row)
    assert row.user_id == uid(p.asha)


def test_the_same_person_registered_twice_is_one_candidate(ew):
    e, p = ew.event, ew.people
    e.reg(e.free, p.priya, age=20)
    e.reg(e.free, p.priya, email="priya.second@example.test", age=10)         # a second registration, another email, the same account

    items = listing(ew, p.admin, e.free)["items"]

    assert sorted(i["can_invite"] for i in items) == [False, True]
    assert [i["reason"] for i in items if not i["can_invite"]] == ["duplicate_registration"]
    resp = create(ew, p.admin, e.free, invite_all_registered=True)
    assert resp.status_code == 201
    assert len([m for m in members_of(ew, p.admin, resp.json()["id"]).values() if m["status"] == "invited"]) == 1


def test_search_and_paging(ew):
    e, p = ew.event, ew.people
    for n in range(7):
        e.reg(e.free, person(f"Attendee {n}", "customer"), age=n)

    page = listing(ew, p.admin, e.free, page_size=3, page=2)

    assert page["total"] == 7 and len(page["items"]) == 3 and page["page"] == 2
    assert [i["name"] for i in listing(ew, p.admin, e.free, q="attendee 5")["items"]] == ["Attendee 5"]
    assert ew.as_user(p.admin).get(f"{API}/events/{e.free.id}/attendees", params={"registration_status": "bogus"}).status_code == 422


def test_group_state_shows_who_is_already_in_a_group(ew):
    e, p = ew.event, ew.people
    e.reg(e.free, p.priya)
    e.reg(e.free, p.ravi)
    group = create(ew, p.admin, e.free, [p.priya]).json()
    accept(ew, p.priya, group["id"])

    anywhere = by_name(listing(ew, p.admin, e.free))
    assert anywhere["Priya Sharma"]["group_state"] == "member" and anywhere["Priya Sharma"]["group_ids"] == [group["id"]]
    assert anywhere["Priya Sharma"]["can_invite"] is True                        # without group_id the list is for a NEW group
    in_this = by_name(listing(ew, p.admin, e.free, group_id=group["id"]))
    assert in_this["Priya Sharma"]["can_invite"] is False and in_this["Priya Sharma"]["reason"] == "already_member"
    assert set(by_name(listing(ew, p.admin, e.free, not_in_group="true"))) == {"Ravi Kumar"}
    assert ew.as_user(p.admin).get(f"{API}/events/{e.free.id}/attendees", params={"group_id": str(uuid4())}).status_code == 404


# =============================================================================================================
# 4. creating the group and inviting
# =============================================================================================================

def test_named_invitees_must_be_eligible_registrants_or_verified_providers(ew):
    e, p = ew.event, ew.people
    e.reg(e.free, p.priya)
    e.reg(e.free, p.stranger, status="cancelled")
    e.reg(e.paid, p.ravi, ticket="general")                                              # unpaid, and for another event anyway

    resp = create(ew, p.admin, e.free, [p.priya, p.stranger, p.ravi, p.asha, p.rao])

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "INVITEE_NOT_ELIGIBLE"
    assert sorted(detail["invalid_user_ids"]) == sorted([p.stranger["id"], p.ravi["id"], p.asha["id"]])
    assert detail["reasons"][p.stranger["id"]] == "registration_cancelled" and detail["reasons"][p.ravi["id"]] == "not_registered"
    assert create(ew, p.admin, e.free, [p.priya, p.rao]).status_code == 201             # a verified Provider is fine


def test_a_walk_in_cannot_be_invited_even_by_id(ew):
    e, p = ew.event, ew.people
    e.reg(e.free, p.priya, source="walk_in")

    resp = create(ew, p.admin, e.free, [p.priya])

    assert resp.status_code == 422 and resp.json()["detail"]["reasons"][p.priya["id"]] == "not_registered"


def test_invite_all_registered_invites_each_eligible_person_once(ew):
    e, p = ew.event, ew.people
    e.reg(e.free, p.priya, age=30)
    e.reg(e.free, p.ravi, status="attended", age=20)
    e.reg(e.free, p.asha, status="cancelled", age=10)
    e.reg(e.free, name="No Account", email="noaccount@example.test", user_id=None)
    e.reg(e.free, name="At The Door", source="walk_in", user_id=uuid4())

    resp = create(ew, p.admin, e.free, [p.priya], invite_all_registered=True)

    assert resp.status_code == 201, resp.text
    invited = {m["user_id"] for m in members_of(ew, p.admin, resp.json()["id"]).values() if m["status"] == "invited"}
    assert invited == {p.priya["id"], p.ravi["id"]}
    assert len([n for n in ew.notifications if n.get("category") == "group_invitation"]) >= 1
    notice = [n for n in ew.notifications if n.get("category") == "group_invitation"][0]
    assert "Wellness Day" in notice["message"] and notice["metadata"]["context_type"] == "event" and notice["metadata"]["context_id"] == str(e.free.id)


def test_exclude_user_ids_leaves_people_out(ew):
    e, p = ew.event, ew.people
    e.reg(e.free, p.priya)
    e.reg(e.free, p.ravi)

    resp = create(ew, p.admin, e.free, invite_all_registered=True, exclude_user_ids=[p.ravi["id"]])

    assert {m["user_id"] for m in members_of(ew, p.admin, resp.json()["id"]).values() if m["status"] == "invited"} == {p.priya["id"]}


def test_the_group_limit_is_enforced_on_the_server(ew, monkeypatch):
    e, p = ew.event, ew.people
    monkeypatch.setattr(settings, "GROUP_MAX_MEMBERS_EVENT", 4)
    attendees = [person(f"Guest {n}", "customer") for n in range(6)]
    for n, user in enumerate(attendees):
        e.reg(e.free, user, age=60 - n)

    too_many = create(ew, p.admin, e.free, attendees[:5])
    assert too_many.status_code == 409 and "at most 4" in too_many.json()["detail"]

    group = create(ew, p.admin, e.free, invite_all_registered=True).json()
    assert group["limits"]["max_members"] == 4
    assert len([m for m in members_of(ew, p.admin, group["id"]).values() if m["status"] == "invited"]) == 3      # owner + 3 = 4

    more = ew.as_user(p.admin).post(f"{API}/{group['id']}/members/invite-registered", json={"all_eligible": True})
    assert more.status_code == 201 and more.json()["invited"] == [] and more.json()["not_invited_group_full"] == 3 and more.json()["free_places"] == 0
    named = ew.as_user(p.admin).post(f"{API}/{group['id']}/members/invite-registered", json={"user_ids": [attendees[5]["id"]]})
    assert named.status_code == 409 and "at most 4" in named.json()["detail"]
    plain = ew.as_user(p.admin).post(f"{API}/{group['id']}/members", json={"invitees": [{"user_id": attendees[5]["id"]}]})
    assert plain.status_code == 409


def test_the_default_limit_is_fifty_including_pending_invitations(ew):
    assert settings.GROUP_MAX_MEMBERS_EVENT == 50


def test_inviting_later_never_sends_a_second_invitation(ew):
    e, p = ew.event, ew.people
    e.reg(e.free, p.priya)
    e.reg(e.free, p.ravi)
    group = create(ew, p.admin, e.free, [p.priya]).json()
    accept(ew, p.priya, group["id"])
    e.reg(e.free, p.asha)                                                   # registered after the group was made

    first = ew.as_user(p.admin).post(f"{API}/{group['id']}/members/invite-registered", json={"all_eligible": True})
    again = ew.as_user(p.admin).post(f"{API}/{group['id']}/members/invite-registered", json={"all_eligible": True})
    named = ew.as_user(p.admin).post(f"{API}/{group['id']}/members/invite-registered", json={"user_ids": [p.priya["id"], p.ravi["id"]]})

    assert first.status_code == 201 and sorted(first.json()["invited"]) == sorted([p.ravi["id"], p.asha["id"]])       # not Priya: already a member
    assert again.json()["invited"] == []
    assert sorted(named.json()["skipped"]) == sorted([p.priya["id"], p.ravi["id"]]) and named.json()["invited"] == []
    assert ew.as_user(p.admin).post(f"{API}/{group['id']}/members/invite-registered", json={}).status_code == 422
    assert ew.as_user(p.admin).post(f"{API}/{group['id']}/members/invite-registered", json={"all_eligible": True, "user_ids": [p.ravi["id"]]}).status_code == 422


def test_eligibility_is_checked_again_when_inviting_later(ew):
    e, p = ew.event, ew.people
    row = e.reg(e.free, p.priya)
    e.reg(e.free, p.ravi)
    group = create(ew, p.admin, e.free, [p.ravi]).json()
    row.status = "cancelled"                                                # Priya cancels after the group was made
    ew.db.commit()

    named = ew.as_user(p.admin).post(f"{API}/{group['id']}/members/invite-registered", json={"user_ids": [p.priya["id"]]})
    plain = ew.as_user(p.admin).post(f"{API}/{group['id']}/members", json={"invitees": [{"user_id": p.priya["id"]}]})
    everyone = ew.as_user(p.admin).post(f"{API}/{group['id']}/members/invite-registered", json={"all_eligible": True})

    assert named.status_code == 422 and named.json()["detail"]["reasons"][p.priya["id"]] == "registration_cancelled"
    assert plain.status_code == 422 and plain.json()["detail"]["code"] == "INVITEE_NOT_ELIGIBLE"
    assert everyone.json()["invited"] == []


def test_invite_registered_works_for_event_groups_only(ew):
    p = ew.people
    group = ew.as_user(p.rao).post(API, json={"name": "Yoga", "kind": "program", "context_type": "program", "context_id": str(uuid4())}).json()

    resp = ew.as_user(p.rao).post(f"{API}/{group['id']}/members/invite-registered", json={"all_eligible": True})

    assert resp.status_code == 422 and "event" in resp.json()["detail"]


def test_a_refund_after_the_invitation_stops_the_person_joining(ew):
    e, p = ew.event, ew.people
    row = e.reg(e.paid, p.priya, payment="paid", ticket="general")
    e.reg(e.paid, p.ravi, payment="paid", ticket="general")
    group = create(ew, p.admin, e.paid, [p.priya, p.ravi]).json()
    order = ew.db.query(EventOrder).filter_by(participant_email=p.priya["email"]).one()
    order.status = order.payment_status = "refunded"
    ew.db.commit()

    refused = ew.as_user(p.priya).post(f"{API}/{group['id']}/accept", json={"consent": True})
    joined = ew.as_user(p.ravi).post(f"{API}/{group['id']}/accept", json={"consent": True})

    assert refused.status_code == 409 and "no longer valid" in refused.json()["detail"]
    assert joined.status_code == 200 and row is not None


def test_a_verified_provider_joins_as_a_member_and_never_becomes_an_admin_or_owner(ew):
    e, p = ew.event, ew.people
    e.reg(e.free, p.priya)
    group = create(ew, p.admin, e.free, [p.priya, p.rao]).json()
    accept(ew, p.rao, group["id"])
    accept(ew, p.priya, group["id"])

    member = members_of(ew, p.admin, group["id"])[p.rao["id"]]
    assert member["kind"] == "provider" and member["role"] == "member"
    promote = ew.as_user(p.admin).patch(f"{API}/{group['id']}/members/{p.rao['id']}/role", json={"role": "admin"})
    handover = ew.as_user(p.admin).post(f"{API}/{group['id']}/transfer-ownership", json={"user_id": p.rao["id"]})
    invite = ew.as_user(p.rao).post(f"{API}/{group['id']}/members/invite-registered", json={"all_eligible": True})
    assert promote.status_code == 403 and "Enterprise Admin" in promote.json()["detail"]
    assert handover.status_code == 403
    assert invite.status_code == 403                                                  # a member cannot invite
    assert members_of(ew, p.admin, group["id"])[p.admin["id"]]["role"] == "owner"


def test_the_providers_list_is_the_enterprises_verified_providers(ew):
    e, p = ew.event, ew.people

    body = ew.as_user(p.admin).get(f"{API}/events/{e.free.id}/providers").json()

    assert {i["user_id"] for i in body["items"]} == {p.rao["id"], p.mehta["id"], p.nair["id"]} and body["total"] == 3
    assert all(set(i) == {"user_id", "name", "registration_number", "is_me"} for i in body["items"])
    assert [i["user_id"] for i in ew.as_user(p.admin).get(f"{API}/events/{e.free.id}/providers", params={"q": "mehta"}).json()["items"]] == [p.mehta["id"]]


# =============================================================================================================
# 5. several groups, and the group's life
# =============================================================================================================

def test_an_event_can_have_several_groups(ew):
    e, p = ew.event, ew.people
    e.reg(e.free, p.priya)
    first = create(ew, p.admin, e.free, [p.priya], name="Morning group").json()
    second = create(ew, p.admin, e.free, [p.priya], name="Afternoon group").json()

    groups = ew.as_user(p.admin).get(f"{API}/events/{e.free.id}/groups").json()

    assert groups["total"] == 2 and {g["id"] for g in groups["items"]} == {first["id"], second["id"]}
    assert all(g["context_name"] == "Wellness Day" for g in groups["items"])
    assert {i["title"]: i["group_count"] for i in ew.as_user(p.admin).get(f"{API}/events").json()["items"]}["Wellness Day"] == 2


def test_messaging_works_in_an_event_group_and_closing_is_manual(ew):
    e, p = ew.event, ew.people
    e.reg(e.free, p.priya)
    group = create(ew, p.admin, e.free, [p.priya], admins_only_posting=False).json()
    accept(ew, p.priya, group["id"])

    assert say(ew, p.admin, group["id"], "Welcome to the day").status_code == 201
    assert say(ew, p.priya, group["id"], "Thank you").status_code == 201
    seen = ew.as_user(p.priya).get(f"{API}/{group['id']}/messages").json()
    assert "Welcome to the day" in str(seen)

    e.free.status = "completed"                                       # the event ends: nothing changes by itself
    ew.db.commit()
    assert say(ew, p.priya, group["id"], "Still here").status_code == 201

    assert ew.as_user(p.admin).post(f"{API}/{group['id']}/close", json={"reason": "The event is over"}).status_code == 200
    closed = say(ew, p.priya, group["id"], "After close")
    assert closed.status_code == 409 and "closed" in closed.json()["detail"]
    assert "Welcome to the day" in str(ew.as_user(p.priya).get(f"{API}/{group['id']}/messages").json())         # the history stays
    assert ew.as_user(p.priya).post(f"{API}/{group['id']}/close", json={}).status_code == 403                    # an attendee cannot close it
    assert ew.as_user(p.admin).post(f"{API}/{group['id']}/reopen", json={"reason": "More to say"}).status_code == 200
    assert say(ew, p.priya, group["id"], "Back again").status_code == 201


def test_a_cancelled_event_keeps_its_group_until_the_admin_closes_it(ew):
    e, p = ew.event, ew.people
    e.reg(e.free, p.priya)
    group = create(ew, p.admin, e.free, [p.priya], admins_only_posting=False).json()
    accept(ew, p.priya, group["id"])
    e.free.status = "cancelled"
    ew.db.commit()

    assert ew.as_user(p.priya).get(f"{API}/{group['id']}").json()["status"] == "open"
    assert say(ew, p.admin, group["id"], "The event is cancelled").status_code == 201
    assert ew.as_user(p.admin).get(f"{API}/events/{e.free.id}/groups").json()["total"] == 1
    assert create(ew, p.admin, e.free).status_code == 409                       # but no new group for a cancelled event


def test_an_attendee_only_sees_a_group_they_accepted(ew):
    e, p = ew.event, ew.people
    e.reg(e.free, p.priya)
    e.reg(e.free, p.ravi)
    group = create(ew, p.admin, e.free, [p.priya]).json()

    assert ew.as_user(p.ravi).get(f"{API}/{group['id']}").status_code == 403           # not invited
    assert ew.as_user(p.priya).get(f"{API}/{group['id']}/messages").status_code == 403    # invited, not accepted
    invitation = ew.as_user(p.priya).get(f"{API}/invitations").json()
    assert invitation["total"] == 1 and "Wellness Day" in invitation["items"][0]["purpose"]


def test_other_enterprises_cannot_touch_an_event_group(ew):
    e, p = ew.event, ew.people
    e.reg(e.free, p.priya)
    group = create(ew, p.admin, e.free, [p.priya]).json()

    assert ew.as_user(p.outsider_admin).post(f"{API}/{group['id']}/members/invite-registered", json={"all_eligible": True}).status_code == 403
    assert ew.as_user(p.outsider_admin).get(f"{API}/{group['id']}/members").status_code == 403
    assert ew.as_user(p.outsider_admin).post(f"{API}/{group['id']}/close", json={}).status_code == 403


def test_a_group_owner_who_is_no_longer_the_events_admin_cannot_invite(ew):
    e, p = ew.event, ew.people
    e.reg(e.free, p.priya)
    group = create(ew, p.admin, e.free).json()
    other_enterprise_now = {**p.admin, "tenant_id": str(OTHER_TENANT)}                  # the same account, moved to another enterprise

    resp = ew.as_user(other_enterprise_now).post(f"{API}/{group['id']}/members/invite-registered", json={"all_eligible": True})

    assert resp.status_code == 403


# =============================================================================================================
# 6. user ids on registrations
# =============================================================================================================

def test_registering_yourself_saves_your_user_id_and_registering_someone_else_does_not(ew):
    from app.schemas.event_schema import EventRegistrationCreate
    from app.services.event_service import create_registration_service

    e, p = ew.event, ew.people

    mine = create_registration_service(ew.db, e.free.id, EventRegistrationCreate(participant_name="Priya Sharma", participant_email=p.priya["email"]), p.priya, None)
    for_priya = create_registration_service(ew.db, e.free.id, EventRegistrationCreate(participant_name="Ravi Kumar", participant_email=p.ravi["email"]), p.admin, None)
    anonymous = create_registration_service(ew.db, e.free.id, EventRegistrationCreate(participant_name="Asha Iyer", participant_email=p.asha["email"]))

    rows = {r.participant_email: r for r in ew.db.query(EventRegistration).all()}
    assert rows[p.priya["email"]].user_id == uid(p.priya)
    assert rows[p.ravi["email"]].user_id is None          # staff registering a customer
    assert rows[p.asha["email"]].user_id is None
    assert mine is not None and for_priya is not None and anonymous is not None


def test_my_registrations_links_the_callers_older_registrations_only(ew):
    from app.services.event_registration_identity import link_caller_registrations

    e, p = ew.event, ew.people
    old = e.reg(e.free, p.priya, user_id=None)
    other_event = e.reg(e.paid, p.priya, user_id=None)
    door = e.reg(e.done, email=p.priya["email"], name="Priya at the door", source="walk_in", user_id=None)
    someone_else = e.reg(e.free, p.ravi, user_id=None)

    assert link_caller_registrations(ew.db, p.priya, None) == 2
    for row in (old, other_event, door, someone_else):
        ew.db.refresh(row)
    assert old.user_id == other_event.user_id == uid(p.priya)
    assert door.user_id is None and someone_else.user_id is None


def test_the_backfill_is_a_dry_run_unless_applied(ew):
    from app.services.event_registration_identity import backfill_user_ids

    e, p = ew.event, ew.people
    row = e.reg(e.free, p.asha, user_id=None)
    e.reg(e.free, name="No Account", email="noaccount@example.test", user_id=None)
    e.reg(e.free, name="At The Door", email=p.asha["email"], source="walk_in", user_id=None)
    e.members.append({"userId": str(uid(p.asha)), "email": p.asha["email"]})

    dry = backfill_user_ids(ew.db)
    ew.db.refresh(row)
    assert dry == {"checked": 2, "resolved": 1, "unresolved": 1, "written": 0} and row.user_id is None
    done = backfill_user_ids(ew.db, apply=True)
    ew.db.refresh(row)
    assert done["written"] == 1 and row.user_id == uid(p.asha)


# =============================================================================================================
# 7. nothing else changed
# =============================================================================================================

def test_training_and_program_groups_still_work_beside_event_groups(ew):
    p = ew.people
    program = ew.as_user(p.rao).post(API, json={"name": "Yoga", "kind": "program", "context_type": "program", "context_id": str(uuid4()),
                                                "invitees": [{"user_id": p.priya["id"]}]})
    care = ew.as_user(p.rao).post(API, json={"name": "Care team", "kind": "care", "context_type": "care_episode", "context_id": str(uuid4())})

    assert program.status_code == 201 and care.status_code == 201
    assert program.json()["limits"]["max_members"] == 50 and care.json()["limits"]["max_members"] == 15
    assert ew.as_user(p.rao).get(API).json()["total"] == 2
