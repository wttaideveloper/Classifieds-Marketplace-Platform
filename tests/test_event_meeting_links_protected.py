"""Event and session meeting links shown as "protected", and what saving a form that carries it does.

* "protected" is deliberate: Event responses never contain a real meeting URL, for anyone, the owner included.
* The owner reads the saved links through GET /events/{id}/meeting-links (or the older /meeting-link and /sessions).
* "protected" sent back in an update means "unchanged": it must never replace a stored URL, and a session that says
  nothing about its link keeps it. Only an explicit URL changes a link and null / "" clears it.
"""
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.models.event_model import Event
from app.utils.event_meeting_links import is_masked_link, keep_stored_session_links
from tests.event_sql_support import (
    API, client_for, customer_user, make_enterprise, make_event, make_registration, make_session, reset_overrides,
    staff_user, super_admin_user,
)

EVENT_LINK = "https://meet.example.com/main-room"
LINK_1, LINK_2 = "https://meet.example.com/day-1", "https://meet.example.com/day-2"
TENANT, OTHER = uuid4(), uuid4()


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr("app.services.notification_triggers._safe_notify", lambda *a, **k: None)
    for target in (
        "app.services.super_admin_identity.fetch_internal_user_by_id",
        "app.services.super_admin_identity.fetch_auth_me_profile",
        "app.services.invigorate_auth_client.fetch_tenant_me_profile",
        "app.services.invigorate_auth_client.fetch_auth_me_profile",
    ):
        monkeypatch.setattr(target, lambda *a, **k: None)
    monkeypatch.setattr("app.services.event_template_mapping.validate_event_submission", lambda *a, **k: None)

    db = make_session()
    enterprise = make_enterprise(db, TENANT)
    day = (datetime.utcnow() + timedelta(days=10)).date().isoformat()
    event = make_event(
        db, TENANT, enterprise, delivery_mode="hybrid", meeting_provider="zoom", meeting_link=EVENT_LINK, status="draft",
        sessions=[
            {"id": "s1", "session_date": day, "start_time": "09:00", "title": "Day 1", "meeting_link": LINK_1},
            {"id": "s2", "session_date": day, "start_time": "14:00", "title": "Day 2", "meeting_link": LINK_2},
        ],
    )
    owner = staff_user(TENANT, "admin")
    yield type("W", (), dict(db=db, event=event, owner=owner, day=day, enterprise=enterprise))
    reset_overrides()
    db.close()


def as_user(w, user):
    return client_for(w.db, user)


def stored(w):
    w.db.expire_all()
    ev = w.db.get(Event, w.event.id)
    return ev.meeting_link, {s.get("id") or s["title"]: s.get("meeting_link") for s in ev.sessions}


def detail(w, user):
    return as_user(w, user).get(f"{API}/{w.event.id}").json()


def edit(w, payload, user=None):
    return as_user(w, user or w.owner).put(f"{API}/{w.event.id}", json=payload)


# --- 1. "protected" is deliberate ---------------------------------------------------------------------------

def test_the_event_details_show_protected_even_to_the_owner(world):
    body = detail(world, world.owner)

    assert body["meeting_link"] == "protected"
    assert [s["meeting_link"] for s in body["sessions"]] == ["protected", "protected"]
    assert EVENT_LINK not in str(body) and LINK_1 not in str(body)


# --- 2. the owner reads the saved links ---------------------------------------------------------------------

def test_the_owner_reads_every_saved_link_in_one_call(world):
    resp = as_user(world, world.owner).get(f"{API}/{world.event.id}/meeting-links")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["meeting_link"] == EVENT_LINK and body["meeting_provider"] == "zoom" and body["delivery_mode"] == "hybrid"
    assert [(s["id"], s["title"], s["meeting_link"]) for s in body["sessions"]] == [("s1", "Day 1", LINK_1), ("s2", "Day 2", LINK_2)]


def test_the_older_endpoints_also_give_the_owner_the_real_links(world):
    client = as_user(world, world.owner)

    assert client.get(f"{API}/{world.event.id}/meeting-link").json()["meeting_link"] == EVENT_LINK
    assert [s["meeting_link"] for s in client.get(f"{API}/{world.event.id}/sessions").json()] == [LINK_1, LINK_2]


def test_a_provider_of_the_owning_business_and_a_super_admin_can_read_them(world):
    for user in (staff_user(TENANT, "provider"), super_admin_user()):
        assert as_user(world, user).get(f"{API}/{world.event.id}/meeting-links").status_code == 200


@pytest.mark.parametrize("who", ["anonymous", "customer", "registered", "other_business_admin", "other_business_provider"])
def test_nobody_else_can_read_them(world, who):
    make_registration(world.db, world.event, "attendee@example.com")
    user = {
        "anonymous": None, "customer": customer_user("stranger@example.com"),
        "registered": customer_user("attendee@example.com"),
        "other_business_admin": staff_user(OTHER, "admin"), "other_business_provider": staff_user(OTHER, "provider"),
    }[who]

    resp = as_user(world, user).get(f"{API}/{world.event.id}/meeting-links")

    assert resp.status_code in (401, 403)
    assert EVENT_LINK not in resp.text and LINK_1 not in resp.text


# --- 3. saving an Event: "protected" means unchanged --------------------------------------------------------

def test_sending_the_event_back_with_protected_everywhere_keeps_every_link(world):
    """What the edit form does when the user changes only the title."""
    body = detail(world, world.owner)
    payload = {"title": "New title", "meeting_link": body["meeting_link"], "sessions": body["sessions"]}

    resp = edit(world, payload)

    assert resp.status_code == 200, resp.text
    assert stored(world) == (EVENT_LINK, {"s1": LINK_1, "s2": LINK_2})
    assert world.db.get(Event, world.event.id).title == "New title"
    assert resp.json()["meeting_link"] == "protected"  # the answer is still masked


def test_an_update_that_does_not_mention_the_links_keeps_them(world):
    assert edit(world, {"title": "Only a title"}).status_code == 200

    assert stored(world) == (EVENT_LINK, {"s1": LINK_1, "s2": LINK_2})


@pytest.mark.parametrize("placeholder", ["protected", "Protected", " PROTECTED "])
def test_the_placeholder_in_any_case_never_replaces_a_link(world, placeholder):
    sessions = [{"id": "s1", "session_date": world.day, "start_time": "09:00", "title": "Day 1", "meeting_link": placeholder},
                {"id": "s2", "session_date": world.day, "start_time": "14:00", "title": "Day 2", "meeting_link": placeholder}]

    assert edit(world, {"meeting_link": placeholder, "sessions": sessions}).status_code == 200

    assert stored(world) == (EVENT_LINK, {"s1": LINK_1, "s2": LINK_2})


def test_a_session_that_leaves_its_link_out_keeps_it(world):
    """Omitting the field is as safe as sending the placeholder."""
    sessions = [{"id": "s1", "session_date": world.day, "start_time": "09:00", "title": "Day 1 renamed"},
                {"id": "s2", "session_date": world.day, "start_time": "14:00", "title": "Day 2"}]

    assert edit(world, {"sessions": sessions}).status_code == 200

    link, links = stored(world)
    assert links == {"s1": LINK_1, "s2": LINK_2}
    assert [s["title"] for s in world.db.get(Event, world.event.id).sessions] == ["Day 1 renamed", "Day 2"]


def test_a_real_url_replaces_a_link(world):
    sessions = [{"id": "s1", "session_date": world.day, "start_time": "09:00", "title": "Day 1", "meeting_link": "https://meet.example.com/new"},
                {"id": "s2", "session_date": world.day, "start_time": "14:00", "title": "Day 2", "meeting_link": "protected"}]

    assert edit(world, {"meeting_link": "https://meet.example.com/new-main", "sessions": sessions}).status_code == 200

    assert stored(world) == ("https://meet.example.com/new-main", {"s1": "https://meet.example.com/new", "s2": LINK_2})


@pytest.mark.parametrize("cleared", [None, ""])
def test_null_or_empty_clears_a_session_link(world, cleared):
    sessions = [{"id": "s1", "session_date": world.day, "start_time": "09:00", "title": "Day 1", "meeting_link": cleared},
                {"id": "s2", "session_date": world.day, "start_time": "14:00", "title": "Day 2", "meeting_link": "protected"}]

    assert edit(world, {"sessions": sessions}).status_code == 200

    _, links = stored(world)
    assert not links["s1"]          # cleared (null or empty), not carried over from storage
    assert links["s2"] == LINK_2    # the other session said "protected": untouched


def test_a_new_session_does_not_inherit_another_sessions_link(world):
    sessions = [{"id": "s1", "session_date": world.day, "start_time": "09:00", "title": "Day 1", "meeting_link": "protected"},
                {"id": "brand-new", "session_date": world.day, "start_time": "16:00", "title": "Extra", "meeting_link": "protected"}]

    assert edit(world, {"sessions": sessions}).status_code == 200

    _, links = stored(world)
    assert links == {"s1": LINK_1, "brand-new": None}


def test_a_removed_session_takes_its_link_with_it_and_the_rest_stay(world):
    sessions = [{"id": "s2", "session_date": world.day, "start_time": "14:00", "title": "Day 2", "meeting_link": "protected"}]

    assert edit(world, {"sessions": sessions}).status_code == 200

    assert stored(world) == (EVENT_LINK, {"s2": LINK_2})


def test_legacy_sessions_without_ids_keep_their_own_links(world):
    """Old rows were stored without ids; the edit form sends them back without ids, and each must keep ITS link."""
    legacy = make_event(
        world.db, TENANT, world.enterprise, delivery_mode="online", meeting_provider="zoom", meeting_link=EVENT_LINK,
        status="draft",
        sessions=[
            {"session_date": world.day, "start_time": "09:00", "title": "Morning", "meeting_link": LINK_1},
            {"session_date": world.day, "start_time": "14:00", "title": "Afternoon", "meeting_link": LINK_2},
        ],
    )
    form = [{"session_date": world.day, "start_time": "09:00", "title": "Morning", "meeting_link": "protected"},
            {"session_date": world.day, "start_time": "14:00", "title": "Afternoon", "meeting_link": "protected"}]

    resp = as_user(world, world.owner).put(f"{API}/{legacy.id}", json={"sessions": form})

    assert resp.status_code == 200, resp.text
    world.db.expire_all()
    assert {s["title"]: s["meeting_link"] for s in world.db.get(Event, legacy.id).sessions} == {"Morning": LINK_1, "Afternoon": LINK_2}


# --- 4. the single-session endpoints -----------------------------------------------------------------------

def test_updating_one_session_with_protected_keeps_its_link(world):
    resp = as_user(world, world.owner).put(f"{API}/{world.event.id}/sessions/s1", json={"title": "Renamed", "meeting_link": "protected"})

    assert resp.status_code == 200, resp.text
    assert stored(world)[1]["s1"] == LINK_1
    assert world.db.get(Event, world.event.id).sessions[0]["title"] == "Renamed"


def test_updating_one_session_without_the_field_keeps_its_link(world):
    as_user(world, world.owner).put(f"{API}/{world.event.id}/sessions/s1", json={"title": "Renamed"})

    assert stored(world)[1]["s1"] == LINK_1


def test_updating_one_session_with_a_url_or_null(world):
    client = as_user(world, world.owner)
    client.put(f"{API}/{world.event.id}/sessions/s1", json={"meeting_link": "https://meet.example.com/other"})
    assert stored(world)[1]["s1"] == "https://meet.example.com/other"

    client.put(f"{API}/{world.event.id}/sessions/s1", json={"meeting_link": None})
    assert stored(world)[1]["s1"] is None


def test_adding_a_session_with_protected_does_not_store_the_word(world):
    resp = as_user(world, world.owner).post(
        f"{API}/{world.event.id}/sessions",
        json={"session_date": world.day, "title": "Added", "meeting_link": "protected"},
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["meeting_link"] in (None, "protected")  # the response is masked either way
    _, links = stored(world)
    added = [v for k, v in links.items() if k not in ("s1", "s2")]
    assert added == [None]


# --- the placeholder check ------------------------------------------------------------------------------------

def test_what_counts_as_the_placeholder():
    for value in ("protected", "Protected", " PROTECTED ", "protected\n"):
        assert is_masked_link(value)
    for value in (None, "", "https://meet.example.com/x", "protected-room", "not protected", 0, ["protected"]):
        assert not is_masked_link(value)


# --- the merge itself ---------------------------------------------------------------------------------------

def test_keep_stored_session_links_unit():
    stored_sessions = [{"id": "a", "meeting_link": "u1"}, {"id": "b", "meeting_link": "u2"}, {"id": "c"}]
    merged = keep_stored_session_links(
        [{"id": "a", "meeting_link": "protected"}, {"id": "b"}, {"id": "c", "meeting_link": "protected"},
         {"id": "d", "meeting_link": "protected"}, {"id": "a", "meeting_link": "new"}, {"id": "b", "meeting_link": None}],
        stored_sessions,
    )

    assert [m.get("meeting_link", "<none>") for m in merged] == ["u1", "u2", "<none>", "<none>", "new", None]


def test_two_legacy_sessions_with_the_same_identity_are_not_guessed():
    twin = {"session_date": "2026-10-20", "start_time": "09:00", "title": "Same"}
    merged = keep_stored_session_links(
        [{**twin, "id": "fresh", "meeting_link": "protected"}],
        [{**twin, "meeting_link": "u1"}, {**twin, "meeting_link": "u2"}],
    )

    assert "meeting_link" not in merged[0]  # ambiguous: better to lose it than to attach the wrong URL
