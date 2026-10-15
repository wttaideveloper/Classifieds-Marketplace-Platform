"""Group chat for Trainings: pick a training, pick the enrolled learners and Providers, create the group, invite more
later. Real routes on an in-memory database.

Covers the web flow's open questions: the link type, learners without a user id, the instructor (never auto-added),
a paged and searchable roster, invitees checked against the training, several groups for one training, an Enterprise
Admin or a Provider as owner, admin promotion / demotion / ownership transfer, and what the Enterprise Admin can see of
a group a Provider created.
"""
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models.enterprise_model import Enterprise
from app.models.training_model import Training, TrainingEnrolment, TrainingWaitlist
from tests.group_chat_support import OTHER_TENANT, TENANT, accept, person, say, uid, world  # noqa: F401

API = "/api/v1/groups"


@pytest.fixture
def tw(world, monkeypatch):
    """The `world` of the group tests plus a clinic's trainings and enrolments."""
    for model in (Enterprise, Training, TrainingEnrolment, TrainingWaitlist):
        model.__table__.create(world.engine, checkfirst=True)
    db, p = world.db, world.people

    ent = Enterprise(tenant_id=TENANT, business_short_name="Clinic", business_legal_name="Clinic Ltd", business_email="c@example.test", status="approved")
    other_ent = Enterprise(tenant_id=OTHER_TENANT, business_short_name="Other", business_legal_name="Other Ltd", business_email="o@example.test", status="approved")
    db.add_all([ent, other_ent])
    db.commit()

    def training(title, enterprise=ent, tenant=TENANT, instructor=None, **kw):
        t = Training(
            id=uuid4(), enterprise_id=enterprise.id, tenant_id=tenant, title=title, category="Wellness", status="published",
            delivery_mode="online", sections=[], assessments=[], assignments=[], instructor_id=instructor,
            instructor_name=kw.pop("instructor_name", None), **kw,
        )
        db.add(t)
        db.commit()
        return t

    yoga = training("Yoga Basics", instructor=uid(p.rao), instructor_name="Dr Rao")
    typed_only = training("Breathwork", instructor=None, instructor_name="Someone Typed In")
    foreign = training("Foreign Course", enterprise=other_ent, tenant=OTHER_TENANT)

    def enrol(t, user=None, name=None, email=None, status="enrolled", user_id="auto", age=0):
        user_id = (uid(user) if user else None) if user_id == "auto" else user_id
        row = TrainingEnrolment(
            training_id=t.id, participant_name=name or (user["name"] if user else "Walk In"),
            participant_email=email or (user["email"] if user else f"{uuid4().hex[:6]}@example.test"), status=status, user_id=user_id,
            created_at=datetime.utcnow() - timedelta(minutes=age),
        )
        db.add(row)
        db.commit()
        return row

    enrol(yoga, p.priya, age=30)
    enrol(yoga, p.ravi, age=20)
    asha_row = enrol(yoga, p.asha, user_id=None, age=10)             # added by name and email: no user id on the row
    enrol(yoga, p.stranger, status="cancelled", age=5)
    enrol(yoga, name="No Account", email="noaccount@example.test", user_id=None, age=1)

    members = []   # what the identity service's member list says
    monkeypatch.setattr("app.services.event_notification_service.list_tenant_users", lambda tenant, access_token=None: list(members))
    members.append({"userId": str(uid(p.asha)), "email": p.asha["email"]})

    world.training = SimpleNamespace_(yoga=yoga, typed_only=typed_only, foreign=foreign, enrol=enrol, asha_row=asha_row, members=members)
    return world


class SimpleNamespace_:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def create(w, user, training, invitees=(), **body):
    payload = {
        "name": "October Yoga Batch", "kind": "program", "context_type": "training", "context_id": str(training.id),
        "invitees": [{"user_id": u["id"]} if isinstance(u, dict) else {"user_id": str(u)} for u in invitees], **body,
    }
    return w.as_user(user).post(API, json=payload)


# =============================================================================================================
# 1. the link type, and who may use a training
# =============================================================================================================

def test_a_training_can_be_the_link_of_a_group(tw):
    t, p = tw.training, tw.people

    resp = create(tw, p.rao, t.yoga)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["context_type"] == "training" and body["context_id"] == str(t.yoga.id) and body["context_name"] == "Yoga Basics"
    assert body["kind"] == "program" and body["my_role"] == "owner"


def test_a_training_group_must_be_a_program_group(tw):
    resp = create(tw, tw.people.rao, tw.training.yoga, kind="care")

    assert resp.status_code == 422 and "program" in resp.text


def test_the_enterprise_admin_can_use_any_training_of_the_clinic(tw):
    assert create(tw, tw.people.admin, tw.training.yoga).status_code == 201
    assert create(tw, tw.people.admin, tw.training.typed_only).status_code == 201


def test_an_unknown_training_is_refused_like_any_other_link(tw):
    resp = tw.as_user(tw.people.admin).post(API, json={
        "name": "X", "kind": "program", "context_type": "training", "context_id": str(uuid4()), "invitees": [],
    })

    assert resp.status_code == 422 and resp.json()["detail"] == "The linked training was not found"


def test_a_training_of_another_clinic_is_refused(tw):
    assert create(tw, tw.people.admin, tw.training.foreign).status_code == 403
    assert create(tw, tw.people.outsider_admin, tw.training.yoga).status_code == 403


def test_a_provider_can_use_only_the_trainings_they_are_assigned_to(tw):
    t, p = tw.training, tw.people

    assert create(tw, p.rao, t.yoga).status_code == 201
    other = create(tw, p.mehta, t.yoga)
    assert other.status_code == 403 and "assigned" in other.json()["detail"]
    assert create(tw, p.rao, t.typed_only).status_code == 403          # the instructor text is not a link


def test_the_assignment_rule_can_be_switched_off(tw, monkeypatch):
    monkeypatch.setattr(settings, "GROUP_PROVIDER_REQUIRES_TRAINING_ASSIGNMENT", False)

    assert create(tw, tw.people.mehta, tw.training.yoga).status_code == 201


def test_learners_and_unverified_providers_cannot_use_a_training(tw):
    for user in (tw.people.priya, tw.people.unverified, tw.people.root):
        assert create(tw, user, tw.training.yoga).status_code == 403, user["name"]
        assert tw.as_user(user).get(f"{API}/trainings").status_code == 403, user["name"]


# =============================================================================================================
# 2. the trainings to choose from
# =============================================================================================================

def test_the_enterprise_admin_sees_the_clinics_trainings_with_counts(tw):
    body = tw.as_user(tw.people.admin).get(f"{API}/trainings").json()

    by_title = {i["title"]: i for i in body["items"]}
    assert set(by_title) == {"Yoga Basics", "Breathwork"}                  # not the other clinic's
    assert by_title["Yoga Basics"]["enrolled_count"] == 4 and by_title["Yoga Basics"]["group_count"] == 0
    assert by_title["Yoga Basics"]["instructor_name"] == "Dr Rao"
    assert by_title["Yoga Basics"]["instructor_user_id"] == tw.people.rao["id"]
    assert by_title["Yoga Basics"]["instructor_is_verified_provider"] is True
    assert by_title["Breathwork"]["instructor_user_id"] is None and by_title["Breathwork"]["instructor_is_verified_provider"] is False


def test_a_provider_sees_only_their_assigned_trainings(tw):
    assert [i["title"] for i in tw.as_user(tw.people.rao).get(f"{API}/trainings").json()["items"]] == ["Yoga Basics"]
    assert tw.as_user(tw.people.mehta).get(f"{API}/trainings").json()["items"] == []


def test_the_training_list_searches_pages_and_counts_groups(tw):
    create(tw, tw.people.rao, tw.training.yoga)
    create(tw, tw.people.admin, tw.training.yoga, name="Second batch")

    found = tw.as_user(tw.people.admin).get(f"{API}/trainings", params={"q": "yoga"}).json()
    assert [i["title"] for i in found["items"]] == ["Yoga Basics"] and found["items"][0]["group_count"] == 2
    page = tw.as_user(tw.people.admin).get(f"{API}/trainings", params={"page": 2, "page_size": 1}).json()
    assert page["total"] == 2 and len(page["items"]) == 1 and page["page"] == 2


# =============================================================================================================
# 3. the enrolled learners: searchable, paged, with the id to invite
# =============================================================================================================

def learners(w, user, training, **params):
    resp = w.as_user(user).get(f"{API}/trainings/{training.id}/learners", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_the_roster_lists_eligible_learners_with_their_user_ids(tw):
    body = learners(tw, tw.people.admin, tw.training.yoga)

    rows = {r["name"]: r for r in body["items"]}
    assert set(rows) == {"Priya Sharma", "Ravi Kumar", "Asha Iyer", "No Account"}      # the cancelled one is not eligible
    assert rows["Priya Sharma"]["user_id"] == tw.people.priya["id"] and rows["Priya Sharma"]["can_invite"] is True
    assert rows["Priya Sharma"]["email"] == tw.people.priya["email"] and rows["Priya Sharma"]["enrolment_status"] == "enrolled"
    assert body["total"] == 4 and body["eligible_total"] == 4 and body["max_members"] == 50 and body["training_title"] == "Yoga Basics"


def test_a_learner_added_by_email_gets_their_user_id_from_the_clinics_members(tw):
    body = learners(tw, tw.people.admin, tw.training.yoga)

    asha = next(r for r in body["items"] if r["name"] == "Asha Iyer")
    assert asha["user_id"] == tw.people.asha["id"] and asha["can_invite"] is True
    tw.db.refresh(tw.training.asha_row)
    assert str(tw.training.asha_row.user_id) == tw.people.asha["id"]            # remembered on the enrolment


def test_a_learner_with_no_account_cannot_be_invited(tw):
    body = learners(tw, tw.people.admin, tw.training.yoga)

    row = next(r for r in body["items"] if r["name"] == "No Account")
    assert row["user_id"] is None and row["can_invite"] is False and row["reason"] == "no_account"


def test_the_roster_is_searchable_by_name_and_email(tw):
    t = tw.training.yoga

    assert [r["name"] for r in learners(tw, tw.people.admin, t, q="priya")["items"]] == ["Priya Sharma"]
    assert [r["name"] for r in learners(tw, tw.people.admin, t, q="ravi.kumar@")["items"]] == ["Ravi Kumar"]
    assert learners(tw, tw.people.admin, t, q="nobody")["items"] == []


def test_the_roster_is_paged_in_name_order(tw):
    t = tw.training.yoga

    first = learners(tw, tw.people.admin, t, page=1, page_size=3)
    second = learners(tw, tw.people.admin, t, page=2, page_size=3)

    assert [r["name"] for r in first["items"]] == ["Asha Iyer", "No Account", "Priya Sharma"] and first["total"] == 4
    assert [r["name"] for r in second["items"]] == ["Ravi Kumar"]


def test_other_enrolment_statuses_can_be_shown_but_not_invited(tw):
    body = learners(tw, tw.people.admin, tw.training.yoga, eligible_only="false")

    cancelled = next(r for r in body["items"] if r["name"] == "Stranger Lee")
    assert cancelled["can_invite"] is False and cancelled["reason"] == "not_eligible_status"
    assert [r["name"] for r in learners(tw, tw.people.admin, tw.training.yoga, enrolment_status="cancelled")["items"]] == ["Stranger Lee"]


def test_the_roster_shows_who_is_already_in_a_group(tw):
    t, p = tw.training, tw.people
    group = create(tw, p.rao, t.yoga, invitees=[p.priya, p.ravi]).json()
    accept(tw, p.priya, group["id"])

    rows = {r["name"]: r for r in learners(tw, p.rao, t.yoga)["items"]}

    assert rows["Priya Sharma"]["group_state"] == "member" and rows["Priya Sharma"]["can_invite"] is False
    assert rows["Priya Sharma"]["reason"] == "already_member" and rows["Priya Sharma"]["group_ids"] == [group["id"]]
    assert rows["Ravi Kumar"]["group_state"] == "invited" and rows["Ravi Kumar"]["reason"] == "already_invited"
    assert rows["Asha Iyer"]["group_state"] == "none" and rows["Asha Iyer"]["can_invite"] is True


def test_not_in_group_hides_everyone_already_in_or_invited_so_late_enrollees_stand_out(tw):
    t, p = tw.training, tw.people
    group = create(tw, p.rao, t.yoga, invitees=[p.priya, p.ravi]).json()
    late = tw.training.enrol(t.yoga, person("Late Joiner", "customer"), age=0)

    names = [r["name"] for r in learners(tw, p.rao, t.yoga, not_in_group="true")["items"]]
    assert names == ["Asha Iyer", "Late Joiner", "No Account"]
    # relative to one group, the same answer; a group of another training is refused
    assert [r["name"] for r in learners(tw, p.rao, t.yoga, not_in_group="true", group_id=group["id"])["items"]] == names
    other = tw.as_user(p.admin).get(f"{API}/trainings/{t.typed_only.id}/learners", params={"group_id": group["id"]})
    assert other.status_code == 404 and late.user_id


def test_a_declined_learner_can_be_invited_again(tw):
    t, p = tw.training, tw.people
    group = create(tw, p.rao, t.yoga, invitees=[p.priya]).json()
    tw.as_user(p.priya).post(f"{API}/{group['id']}/decline", json={})

    row = next(r for r in learners(tw, p.rao, t.yoga)["items"] if r["name"] == "Priya Sharma")

    assert row["group_state"] == "declined" and row["can_invite"] is True


def test_the_roster_is_for_the_clinics_staff_only(tw):
    url = f"{API}/trainings/{tw.training.yoga.id}/learners"

    assert tw.as_user(tw.people.priya).get(url).status_code == 403
    assert tw.as_user(tw.people.outsider_admin).get(url).status_code == 403
    assert tw.as_user(tw.people.mehta).get(url).status_code == 403              # a Provider not assigned to it
    assert tw.as_user(tw.people.rao).get(url).status_code == 200
    assert tw.as_user(tw.people.admin).get(f"{API}/trainings/{uuid4()}/learners").status_code == 404


# =============================================================================================================
# 4. the Providers to add; the instructor text is only information
# =============================================================================================================

def test_the_providers_list_marks_the_assigned_instructor_and_does_not_add_anyone(tw):
    t, p = tw.training, tw.people

    body = tw.as_user(p.admin).get(f"{API}/trainings/{t.yoga.id}/providers").json()

    assert [i["name"] for i in body["items"]] == ["Dr Rao", "Dr Mehta", "Dr Nair"]       # the instructor first, then by name
    assert body["items"][0]["is_training_instructor"] is True and body["instructor_name"] == "Dr Rao"
    assert "Dr Unverified" not in str(body)                                            # only verified Providers
    group = create(tw, p.admin, t.yoga).json()
    members = tw.as_user(p.admin).get(f"{API}/{group['id']}/members").json()["members"]
    assert [m["user_id"] for m in members] == [p.admin["id"]]                          # the instructor was not added


def test_a_training_with_only_a_typed_instructor_name_has_no_instructor_match(tw):
    body = tw.as_user(tw.people.admin).get(f"{API}/trainings/{tw.training.typed_only.id}/providers").json()

    assert body["instructor_name"] == "Someone Typed In" and not any(i["is_training_instructor"] for i in body["items"])


def test_providers_are_searchable_and_the_caller_is_marked(tw):
    body = tw.as_user(tw.people.rao).get(f"{API}/trainings/{tw.training.yoga.id}/providers", params={"q": "mehta"}).json()

    assert [i["name"] for i in body["items"]] == ["Dr Mehta"]
    mine = tw.as_user(tw.people.rao).get(f"{API}/trainings/{tw.training.yoga.id}/providers").json()["items"]
    assert next(i for i in mine if i["name"] == "Dr Rao")["is_me"] is True


def test_the_other_clinics_providers_are_not_offered(tw):
    from app.models.group_chat_model import VerifiedProvider

    foreign_doc = person("Dr Foreign", "provider", tenant=OTHER_TENANT)
    tw.db.add(VerifiedProvider(user_id=uid(foreign_doc), tenant_id=OTHER_TENANT, status="verified"))
    tw.db.commit()

    body = tw.as_user(tw.people.admin).get(f"{API}/trainings/{tw.training.yoga.id}/providers").json()

    assert "Dr Foreign" not in str(body)


# =============================================================================================================
# 5. creating the group: invitees are checked against the training
# =============================================================================================================

def test_the_enterprise_admin_creates_a_group_with_learners_and_a_provider(tw):
    t, p = tw.training, tw.people

    resp = create(tw, p.admin, t.yoga, invitees=[p.priya, p.ravi, p.mehta])

    assert resp.status_code == 201, resp.text
    group = resp.json()
    assert group["my_role"] == "owner" and group["created_by"] == p.admin["id"]
    members = {m["user_id"]: m for m in tw.as_user(p.admin).get(f"{API}/{group['id']}/members").json()["members"]}
    assert members[p.admin["id"]]["role"] == "owner" and members[p.admin["id"]]["status"] == "active"
    assert members[p.priya["id"]]["status"] == "invited" and members[p.priya["id"]]["kind"] == "patient"
    assert members[p.mehta["id"]]["status"] == "invited" and members[p.mehta["id"]]["kind"] == "provider"
    assert {n["category"] for n in tw.notifications} >= {"group_invitation"} or tw.notifications


def test_the_enterprise_admin_can_chat_in_their_own_group_with_their_login(tw):
    t, p = tw.training, tw.people
    group = create(tw, p.admin, t.yoga, invitees=[p.priya]).json()
    accept(tw, p.priya, group["id"])

    sent = say(tw, p.admin, group["id"], "Welcome to the batch")
    seen = tw.as_user(p.priya).get(f"{API}/{group['id']}/messages").json()

    assert sent.status_code == 201, sent.text
    assert any(m["content"] == "Welcome to the batch" for m in seen["items"])


def test_someone_who_is_not_enrolled_cannot_be_invited(tw):
    resp = create(tw, tw.people.rao, tw.training.yoga, invitees=[tw.people.priya, tw.people.stranger])

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["invalid_user_ids"] == [tw.people.stranger["id"]] and "not enrolled" in detail["message"]
    assert tw.db.query(__import__("app.models.chat_model", fromlist=["Conversation"]).Conversation).count() == 0


def test_a_cancelled_enrolment_and_an_unverified_provider_cannot_be_invited(tw):
    p = tw.people
    resp = create(tw, p.rao, tw.training.yoga, invitees=[p.unverified])
    assert resp.status_code == 422 and resp.json()["detail"]["invalid_user_ids"] == [p.unverified["id"]]
    gone = create(tw, p.rao, tw.training.yoga, invitees=[p.stranger])        # enrolment status: cancelled
    assert gone.status_code == 422


def test_a_learner_of_another_training_cannot_be_invited(tw):
    p = tw.training
    other = person("Other Learner", "customer")
    tw.training.enrol(p.typed_only, other)

    assert create(tw, tw.people.rao, p.yoga, invitees=[other]).status_code == 422


def test_the_group_limit_counts_the_owner_and_everyone_invited(tw, monkeypatch):
    monkeypatch.setattr(settings, "GROUP_MAX_MEMBERS_PROGRAM", 3)
    p = tw.people

    ok = create(tw, p.rao, tw.training.yoga, invitees=[p.priya, p.ravi])
    over = create(tw, p.rao, tw.training.yoga, invitees=[p.priya, p.ravi, p.asha])

    assert ok.status_code == 201 and over.status_code == 409


def test_invite_all_enrolled_fills_the_group_with_the_eligible_learners(tw):
    p = tw.people

    resp = create(tw, p.rao, tw.training.yoga, invite_all_enrolled=True)

    assert resp.status_code == 201, resp.text
    members = tw.as_user(p.rao).get(f"{API}/{resp.json()['id']}/members").json()["members"]
    invited = {m["user_id"] for m in members if m["status"] == "invited"}
    assert invited == {p.priya["id"], p.ravi["id"], p.asha["id"]}              # not the cancelled one, not the no-account one


def test_invite_all_enrolled_stops_at_the_group_limit_and_leaves_out_the_excluded(tw, monkeypatch):
    monkeypatch.setattr(settings, "GROUP_MAX_MEMBERS_PROGRAM", 3)
    p = tw.people

    resp = create(tw, p.rao, tw.training.yoga, invite_all_enrolled=True, exclude_user_ids=[p.priya["id"]])

    members = tw.as_user(p.rao).get(f"{API}/{resp.json()['id']}/members").json()["members"]
    assert {m["user_id"] for m in members if m["status"] == "invited"} == {p.ravi["id"], p.asha["id"]}   # oldest enrolments first
    assert resp.json()["member_count"] == 1


def test_invite_all_enrolled_is_for_training_groups_only(tw):
    resp = tw.as_user(tw.people.rao).post(API, json={
        "name": "X", "kind": "program", "context_type": "program", "context_id": str(uuid4()), "invite_all_enrolled": True,
    })

    assert resp.status_code == 422


def test_the_group_name_rules_still_apply(tw):
    resp = create(tw, tw.people.rao, tw.training.yoga, invitees=[tw.people.priya], name="Priya's batch")

    assert resp.status_code == 422


def test_several_groups_can_follow_the_same_training(tw):
    t, p = tw.training, tw.people
    a = create(tw, p.rao, t.yoga, invitees=[p.priya]).json()
    b = create(tw, p.rao, t.yoga, invitees=[p.priya], name="Weekend batch").json()

    assert a["id"] != b["id"]
    listed = tw.as_user(p.rao).get(f"{API}/trainings/{t.yoga.id}/groups").json()
    assert {g["name"] for g in listed["items"]} == {"October Yoga Batch", "Weekend batch"} and listed["total"] == 2
    assert all(g["context_name"] == "Yoga Basics" for g in listed["items"])


# =============================================================================================================
# 6. inviting more people later, including those who enrolled afterwards
# =============================================================================================================

def test_a_group_admin_can_invite_chosen_learners_by_id(tw):
    t, p = tw.training, tw.people
    group = create(tw, p.rao, t.yoga, invitees=[p.priya]).json()

    resp = tw.as_user(p.rao).post(f"{API}/{group['id']}/members/invite-enrolled", json={"user_ids": [p.ravi["id"], p.priya["id"]]})

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["invited"] == [p.ravi["id"]] and body["skipped"] == [p.priya["id"]] and body["free_places"] == 47


def test_chosen_ids_are_checked_against_the_training(tw):
    group = create(tw, tw.people.rao, tw.training.yoga).json()

    resp = tw.as_user(tw.people.rao).post(f"{API}/{group['id']}/members/invite-enrolled", json={"user_ids": [tw.people.stranger["id"]]})

    assert resp.status_code == 422 and resp.json()["detail"]["invalid_user_ids"] == [tw.people.stranger["id"]]


def test_the_plain_invite_endpoint_checks_the_training_too(tw):
    group = create(tw, tw.people.rao, tw.training.yoga).json()

    bad = tw.as_user(tw.people.rao).post(f"{API}/{group['id']}/members", json={"invitees": [{"user_id": tw.people.stranger["id"]}]})
    good = tw.as_user(tw.people.rao).post(f"{API}/{group['id']}/members", json={"invitees": [{"user_id": tw.people.priya["id"]}]})

    assert bad.status_code == 422 and good.status_code == 201


def test_all_eligible_invites_the_late_enrollees_and_skips_the_ones_already_in(tw):
    t, p = tw.training, tw.people
    group = create(tw, p.rao, t.yoga, invitees=[p.priya]).json()
    late = person("Late Joiner", "customer")
    tw.training.enrol(t.yoga, late)

    resp = tw.as_user(p.rao).post(f"{API}/{group['id']}/members/invite-enrolled", json={"all_eligible": True})

    body = resp.json()
    assert resp.status_code == 201 and set(body["invited"]) == {p.ravi["id"], p.asha["id"], late["id"]}
    assert body["no_account"] == 1 and body["not_invited_group_full"] == 0 and body["free_places"] == 45
    again = tw.as_user(p.rao).post(f"{API}/{group['id']}/members/invite-enrolled", json={"all_eligible": True}).json()
    assert again["invited"] == []


def test_all_eligible_reports_who_did_not_fit(tw, monkeypatch):
    monkeypatch.setattr(settings, "GROUP_MAX_MEMBERS_PROGRAM", 3)
    group = create(tw, tw.people.rao, tw.training.yoga).json()

    body = tw.as_user(tw.people.rao).post(f"{API}/{group['id']}/members/invite-enrolled", json={"all_eligible": True}).json()

    assert len(body["invited"]) == 2 and body["not_invited_group_full"] == 1 and body["free_places"] == 0


def test_invite_enrolled_needs_one_way_to_choose(tw):
    group = create(tw, tw.people.rao, tw.training.yoga).json()
    url = f"{API}/{group['id']}/members/invite-enrolled"

    assert tw.as_user(tw.people.rao).post(url, json={}).status_code == 422
    assert tw.as_user(tw.people.rao).post(url, json={"all_eligible": True, "user_ids": [tw.people.priya["id"]]}).status_code == 422


def test_only_the_owner_or_an_admin_can_invite_and_only_in_a_training_group(tw):
    t, p = tw.training, tw.people
    group = create(tw, p.rao, t.yoga, invitees=[p.priya]).json()
    accept(tw, p.priya, group["id"])
    url = f"{API}/{group['id']}/members/invite-enrolled"

    assert tw.as_user(p.priya).post(url, json={"all_eligible": True}).status_code == 403
    plain = tw.as_user(p.rao).post(API, json={"name": "Plain", "kind": "program", "context_type": "program", "context_id": str(uuid4())}).json()
    assert tw.as_user(p.rao).post(f"{API}/{plain['id']}/members/invite-enrolled", json={"all_eligible": True}).status_code == 422


def test_learners_join_only_by_accepting_and_see_the_training_in_the_invitation(tw):
    t, p = tw.training, tw.people
    group = create(tw, p.rao, t.yoga, invitees=[p.priya]).json()

    before = tw.as_user(p.priya).get(f"{API}/{group['id']}/messages")
    invitation = tw.as_user(p.priya).get(f"{API}/invitations").json()["items"][0]
    accept(tw, p.priya, group["id"], display_name="Priya S.")

    assert before.status_code == 403
    assert 'learners of the training "Yoga Basics"' in invitation["purpose"]
    mine = tw.as_user(p.priya).get(API).json()["items"]
    assert [g["context_name"] for g in mine] == ["Yoga Basics"]


# =============================================================================================================
# 7. owner and admin management (a Provider as owner, an Enterprise Admin as owner)
# =============================================================================================================

def joined(tw, owner, members):
    group = create(tw, owner, tw.training.yoga, invitees=members).json()
    for m in members:
        accept(tw, m, group["id"])
    return group


def test_the_owner_can_make_a_joined_provider_an_admin_and_take_it_back(tw):
    p = tw.people
    group = joined(tw, p.admin, [p.priya, p.mehta])
    role = lambda body: tw.as_user(p.admin).patch(f"{API}/{group['id']}/members/{p.mehta['id']}/role", json=body)

    assert role({"role": "admin"}).status_code == 200
    members = {m["user_id"]: m for m in tw.as_user(p.admin).get(f"{API}/{group['id']}/members").json()["members"]}
    assert members[p.mehta["id"]]["role"] == "admin" and members[p.admin["id"]]["role"] == "owner"
    assert role({"role": "member"}).status_code == 422                                  # a reason is needed to take it away
    assert role({"role": "member", "reason": "No longer teaching"}).status_code == 200


def test_a_learner_cannot_be_made_an_admin(tw):
    p = tw.people
    group = joined(tw, p.rao, [p.priya])

    resp = tw.as_user(p.rao).patch(f"{API}/{group['id']}/members/{p.priya['id']}/role", json={"role": "admin"})

    assert resp.status_code == 422


def test_a_group_can_have_several_admins_up_to_the_limit(tw, monkeypatch):
    monkeypatch.setattr(settings, "GROUP_MAX_ADMINS", 2)
    p = tw.people
    group = joined(tw, p.admin, [p.rao, p.mehta, p.nair])
    make = lambda who: tw.as_user(p.admin).patch(f"{API}/{group['id']}/members/{who['id']}/role", json={"role": "admin"}).status_code

    assert [make(p.rao), make(p.mehta), make(p.nair)] == [200, 200, 409]


def test_an_admin_can_invite_and_remove_but_not_remove_the_owner(tw):
    p = tw.people
    group = joined(tw, p.rao, [p.priya, p.mehta])
    tw.as_user(p.rao).patch(f"{API}/{group['id']}/members/{p.mehta['id']}/role", json={"role": "admin"})

    invited = tw.as_user(p.mehta).post(f"{API}/{group['id']}/members/invite-enrolled", json={"user_ids": [p.ravi["id"]]})
    removed = tw.as_user(p.mehta).delete(f"{API}/{group['id']}/members/{p.priya['id']}")
    owner = tw.as_user(p.mehta).delete(f"{API}/{group['id']}/members/{p.rao['id']}")
    renamed = tw.as_user(p.mehta).patch(f"{API}/{group['id']}", json={"description": "New description"})

    assert invited.status_code == 201 and removed.status_code == 200 and renamed.status_code == 200
    assert owner.status_code == 403
    assert tw.as_user(p.mehta).patch(f"{API}/{group['id']}/members/{p.mehta['id']}/role", json={"role": "member"}).status_code == 403


def test_a_learner_cannot_manage_the_group(tw):
    p = tw.people
    group = joined(tw, p.rao, [p.priya, p.ravi])

    assert tw.as_user(p.priya).delete(f"{API}/{group['id']}/members/{p.ravi['id']}").status_code == 403
    assert tw.as_user(p.priya).patch(f"{API}/{group['id']}/members/{p.ravi['id']}/role", json={"role": "admin"}).status_code == 403
    assert tw.as_user(p.priya).post(f"{API}/{group['id']}/close", json={}).status_code == 403


def test_the_owner_hands_the_group_to_another_verified_provider_before_leaving(tw):
    p = tw.people
    group = joined(tw, p.rao, [p.mehta])
    url = f"{API}/{group['id']}"

    assert tw.as_user(p.rao).post(f"{url}/leave").status_code == 409
    assert tw.as_user(p.rao).post(f"{url}/transfer-ownership", json={"user_id": p.mehta["id"]}).status_code == 200
    assert tw.as_user(p.rao).post(f"{url}/leave").status_code == 200
    members = {m["user_id"]: m["role"] for m in tw.as_user(p.mehta).get(f"{url}/members").json()["members"]}
    assert members == {p.mehta["id"]: "owner"}


def test_ownership_cannot_go_to_a_learner(tw):
    p = tw.people
    group = joined(tw, p.rao, [p.priya])

    assert tw.as_user(p.rao).post(f"{API}/{group['id']}/transfer-ownership", json={"user_id": p.priya["id"]}).status_code == 422


def test_compliance_can_name_a_new_owner_when_the_owner_is_gone(tw):
    p = tw.people
    group = joined(tw, p.rao, [p.mehta])

    resp = tw.as_user(p.admin).post(f"{API}/{group['id']}/reassign-owner", json={"user_id": p.mehta["id"], "reason": "Dr Rao left the clinic"})

    assert resp.status_code == 200
    assert tw.as_user(p.mehta).get(f"{API}/{group['id']}").json()["my_role"] == "owner"
    assert tw.as_user(p.outsider_admin).post(f"{API}/{group['id']}/reassign-owner", json={"user_id": p.mehta["id"], "reason": "not mine"}).status_code == 403


def test_the_owner_can_close_the_group(tw):
    group = create(tw, tw.people.rao, tw.training.yoga).json()

    closed = tw.as_user(tw.people.rao).post(f"{API}/{group['id']}/close", json={"reason": "Training ended"})

    assert closed.status_code == 200 and closed.json()["status"] == "closed"


# =============================================================================================================
# 8. a Provider's group: the Enterprise Admin sees the details, not the messages
# =============================================================================================================

def test_the_enterprise_admin_is_not_a_member_of_a_providers_group_and_cannot_read_it(tw):
    t, p = tw.training, tw.people
    group = joined(tw, p.rao, [p.priya])
    say(tw, p.rao, group["id"], "Private class note")

    details = tw.as_user(p.admin).get(f"{API}/{group['id']}")
    messages = tw.as_user(p.admin).get(f"{API}/{group['id']}/messages")
    members = tw.as_user(p.admin).get(f"{API}/{group['id']}/members")
    sent = say(tw, p.admin, group["id"], "Can I write?")

    assert details.status_code == 200 and details.json()["my_role"] is None and details.json()["context_name"] == "Yoga Basics"
    assert messages.status_code == 403 and members.status_code == 403 and sent.status_code == 403
    listed = tw.as_user(p.admin).get(f"{API}/trainings/{t.yoga.id}/groups").json()
    assert [g["id"] for g in listed["items"]] == [group["id"]]                           # the admin finds it for compliance
    assert [g["id"] for g in tw.as_user(p.admin).get(f"{API}/compliance/groups").json()["items"]] == [group["id"]]
    assert tw.as_user(p.admin).get(API).json()["items"] == []                            # but it is not in their own chat list


def test_another_provider_does_not_see_a_providers_group_in_the_training_list(tw, monkeypatch):
    monkeypatch.setattr(settings, "GROUP_PROVIDER_REQUIRES_TRAINING_ASSIGNMENT", False)
    create(tw, tw.people.rao, tw.training.yoga)

    assert tw.as_user(tw.people.mehta).get(f"{API}/trainings/{tw.training.yoga.id}/groups").json()["items"] == []


def test_a_clinic_admin_of_another_clinic_sees_nothing(tw):
    group = create(tw, tw.people.rao, tw.training.yoga).json()

    assert tw.as_user(tw.people.outsider_admin).get(f"{API}/{group['id']}").status_code == 403
    assert tw.as_user(tw.people.outsider_admin).get(f"{API}/trainings/{tw.training.yoga.id}/groups").status_code == 403


def test_the_enterprise_admin_can_be_invited_into_a_providers_group_only_when_verified(tw):
    p = tw.people
    group = create(tw, p.rao, tw.training.yoga).json()
    url = f"{API}/{group['id']}/members"

    refused = tw.as_user(p.rao).post(f"{url}/invite-enrolled", json={"user_ids": [p.admin["id"]]})
    assert refused.status_code == 422                       # not a learner of the training and not a verified Provider

    from app.models.group_chat_model import VerifiedProvider

    tw.db.add(VerifiedProvider(user_id=uid(p.admin), tenant_id=TENANT, status="verified"))
    tw.db.commit()
    ok = tw.as_user(p.rao).post(f"{url}/invite-enrolled", json={"user_ids": [p.admin["id"]]})
    assert ok.status_code == 201 and ok.json()["invited"] == [p.admin["id"]]
    accept(tw, p.admin, group["id"])
    assert tw.as_user(p.admin).get(f"{API}/{group['id']}").json()["my_kind"] == "provider"
    assert say(tw, p.admin, group["id"], "Hello from the admin").status_code == 201
