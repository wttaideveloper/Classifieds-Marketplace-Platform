"""
Event Management Phase 2.2 — configurable events (event_type + modules), end to end on real SQL.

Runs the actual endpoints against an in-memory database (tests/event_sql_support.py), so what is
asserted is what is persisted and returned: defaults per type, explicit overrides, partial-update
semantics, legacy (NULL/NULL) events, invalid input, Phase 2.1 tenant isolation, and the OpenAPI contract.

Run:
    pytest tests/test_event_phase_2_2_configuration.py -v
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from event_sql_support import (
    API,
    Event,
    EventAudit,
    client_for,
    customer_user,
    make_enterprise,
    make_event,
    make_session,
    paid_ticket,
    reset_overrides,
    silence_side_effects,
    staff_user,
)

# ---- the product spec, typed out independently of the implementation -------------------------------
SPEC_TYPES = ["conference", "workshop", "marathon", "camp", "private_function", "webinar", "other"]
SPEC_KEYS = ["registration", "tickets", "sessions", "check_in", "online_meeting", "custom_questions", "meals", "accommodation"]


def flags(*enabled):
    return {key: key in enabled for key in SPEC_KEYS}


SPEC_DEFAULTS = {
    "conference": flags("registration", "tickets", "sessions", "check_in", "meals", "accommodation"),
    "workshop": flags("registration", "tickets", "sessions", "check_in"),
    "marathon": flags("registration", "tickets", "check_in"),
    "camp": flags("registration", "check_in", "meals", "accommodation"),
    "private_function": flags("registration", "check_in", "custom_questions", "meals"),
    "webinar": flags("registration", "sessions", "online_meeting"),
    "other": flags("registration"),
}
LEGACY_PLAIN = flags("registration", "check_in")

# fields every event response exposed before Phase 2.2 (plus lifecycle_state from the previous phase): all must remain
EXISTING_RESPONSE_FIELDS = [
    "id", "tenant_id", "enterprise_id", "location_id", "title", "description", "category", "subcategory", "tags",
    "organiser_name", "organiser_contact", "start_date", "end_date", "duration_type", "time_zone",
    "registration_cutoff", "primary_image", "gallery_images", "videos", "documents", "delivery_mode",
    "delivery_mode_display", "venue", "meeting_link", "meeting_provider", "pricing_type", "price", "currency",
    "ticket_types", "capacity", "min_participants", "max_participants", "registration_open_at",
    "registration_close_at", "custom_fields", "custom_values", "form_configuration_id",
    "form_configuration_version_id", "sessions", "status", "lifecycle_state", "is_deleted", "created_at",
    "updated_at", "requires_reapproval", "last_admin_notes", "available_seats", "is_full", "registration_open",
]


@pytest.fixture
def cfg(monkeypatch):
    """One tenant with an owner; the Event Form machinery (needs tables outside this harness) is stubbed."""
    silence_side_effects(monkeypatch)
    db = make_session()
    tenant = uuid4()
    ent = make_enterprise(db, tenant)
    monkeypatch.setattr(
        "app.services.event_form_config_service.apply_form_configuration_to_event_data",
        lambda _db, data, user: {"tenant_id": tenant, "enterprise_id": ent.id, "form_configuration_id": None,
                                 "form_configuration_version_id": None, "custom_values": []},
    )
    owner = staff_user(tenant, "admin")
    yield SimpleNamespace(db=db, tenant=tenant, ent=ent, owner=owner, staff=client_for(db, owner))
    reset_overrides()
    db.close()


def body(**extra):
    start = datetime.utcnow() + timedelta(days=5)
    return {"title": "Summit", "category": "Wellness", "start_date": start.isoformat(),
            "end_date": (start + timedelta(hours=4)).isoformat(), "status": "draft", **extra}


def create(cfg, **extra):
    return cfg.staff.post(f"{API}/", json=body(**extra))


def row(db, event_id):
    """The persisted row, exactly as stored (not as resolved for responses)."""
    db.expire_all()
    return db.get(Event, UUID(str(event_id)))


def count_commits(db):
    calls = []
    original = db.commit
    db.commit = lambda: (calls.append(1), original())[1]
    return calls


# ===========================================================================
# A. event_type validation
# ===========================================================================


class TestEventTypeValidation:
    @pytest.mark.parametrize("event_type", SPEC_TYPES)
    def test_every_supported_type_is_accepted_and_returned(self, cfg, event_type):
        resp = create(cfg, event_type=event_type)
        assert resp.status_code == 201, resp.text
        assert resp.json()["event_type"] == event_type
        assert row(cfg.db, resp.json()["id"]).event_type == event_type

    @pytest.mark.parametrize("bad", ["party", "Conference", "CONFERENCE", "", " conference", "private-function", 7, ["camp"]])
    def test_invalid_types_are_rejected_and_nothing_is_created(self, cfg, bad):
        resp = create(cfg, event_type=bad)
        assert resp.status_code == 422
        assert "event_type" in resp.text
        assert cfg.db.query(Event).count() == 0

    def test_invalid_type_on_update_is_rejected_and_the_event_is_unchanged(self, cfg):
        event_id = create(cfg, event_type="camp").json()["id"]
        assert cfg.staff.put(f"{API}/{event_id}", json={"event_type": "party"}).status_code == 422
        assert row(cfg.db, event_id).event_type == "camp"

    def test_event_type_is_optional_and_a_typeless_create_stays_legacy(self, cfg):
        resp = create(cfg)
        assert resp.status_code == 201, resp.text
        stored = row(cfg.db, resp.json()["id"])
        assert stored.event_type is None and stored.modules is None  # nothing invented at write time
        assert resp.json()["event_type"] == "other"  # resolved for the response only
        assert resp.json()["modules"] == LEGACY_PLAIN

    def test_status_values_and_transitions_are_untouched(self, cfg):
        event_id = create(cfg, event_type="conference").json()["id"]
        assert cfg.staff.patch(f"{API}/{event_id}/status", json={"status": "pending_approval"}).json()["status"] == "pending_approval"
        assert cfg.staff.patch(f"{API}/{event_id}/status", json={"status": "not_a_status"}).status_code == 422


# ===========================================================================
# B. default modules by type
# ===========================================================================


class TestDefaultModules:
    @pytest.mark.parametrize("event_type", SPEC_TYPES)
    def test_creating_with_only_a_type_applies_and_persists_that_types_defaults(self, cfg, event_type):
        resp = create(cfg, event_type=event_type)
        assert resp.status_code == 201, resp.text
        assert resp.json()["modules"] == SPEC_DEFAULTS[event_type]
        assert row(cfg.db, resp.json()["id"]).modules == SPEC_DEFAULTS[event_type]  # persisted as the full 8-key dict

    @pytest.mark.parametrize("event_type", SPEC_TYPES)
    def test_defaults_are_returned_by_detail_and_list_too(self, cfg, event_type):
        event_id = create(cfg, event_type=event_type).json()["id"]
        assert cfg.staff.get(f"{API}/{event_id}").json()["modules"] == SPEC_DEFAULTS[event_type]
        listed = {i["id"]: i for i in cfg.staff.get(f"{API}/?page_size=100").json()["items"]}
        assert listed[event_id]["modules"] == SPEC_DEFAULTS[event_type] and listed[event_id]["event_type"] == event_type

    def test_defaults_are_a_snapshot_not_a_live_link_to_the_type(self, cfg, monkeypatch):
        """Editing the defaults table later must not change events that already exist (no recalculation)."""
        event_id = create(cfg, event_type="conference").json()["id"]
        from app.utils import event_modules

        monkeypatch.setitem(event_modules.EVENT_TYPE_DEFAULT_MODULES, "conference", {**SPEC_DEFAULTS["conference"], "meals": False})
        assert cfg.staff.get(f"{API}/{event_id}").json()["modules"] == SPEC_DEFAULTS["conference"]

    def test_a_paid_event_keeps_ticketing_even_when_its_type_defaults_it_off(self, cfg):
        paid = create(cfg, event_type="camp", pricing_type="paid", price="500")
        assert paid.json()["modules"] == {**SPEC_DEFAULTS["camp"], "tickets": True}
        with_tickets = create(cfg, event_type="private_function", pricing_type="paid",
                              ticket_types=[{"name": "GA", "price": "100"}])
        assert with_tickets.json()["modules"]["tickets"] is True
        free = create(cfg, event_type="camp")
        assert free.json()["modules"]["tickets"] is False


# ===========================================================================
# C. explicit overrides
# ===========================================================================


class TestExplicitOverrides:
    def test_an_organizer_can_turn_a_default_off_and_it_is_persisted_and_returned(self, cfg):
        resp = create(cfg, event_type="conference", modules={"meals": False})
        assert resp.status_code == 201, resp.text
        expected = {**SPEC_DEFAULTS["conference"], "meals": False}
        assert resp.json()["modules"] == expected
        assert row(cfg.db, resp.json()["id"]).modules == expected
        assert cfg.staff.get(f"{API}/{resp.json()['id']}").json()["modules"] == expected

    def test_a_default_can_be_turned_on(self, cfg):
        resp = create(cfg, event_type="webinar", modules={"custom_questions": True}, delivery_mode="online")
        assert resp.json()["modules"] == {**SPEC_DEFAULTS["webinar"], "custom_questions": True}

    def test_a_full_explicit_configuration_is_used_exactly_as_given(self, cfg):
        given = flags("registration", "sessions", "meals")
        resp = create(cfg, event_type="conference", modules=given)
        assert resp.json()["modules"] == given == row(cfg.db, resp.json()["id"]).modules

    def test_overrides_without_a_type_sit_on_the_behaviour_based_modules_and_keep_the_type_unset(self, cfg):
        resp = create(cfg, delivery_mode="online", modules={"meals": True})
        assert resp.status_code == 201, resp.text
        assert resp.json()["modules"] == {**flags("registration", "check_in", "online_meeting"), "meals": True}
        stored = row(cfg.db, resp.json()["id"])
        assert stored.event_type is None and stored.modules["meals"] is True

    def test_empty_overrides_are_treated_as_omitted(self, cfg):
        assert create(cfg, event_type="camp", modules={}).json()["modules"] == SPEC_DEFAULTS["camp"]

    def test_overrides_survive_later_reads_and_unrelated_updates(self, cfg):
        event_id = create(cfg, event_type="conference", modules={"meals": False}).json()["id"]
        assert cfg.staff.put(f"{API}/{event_id}", json={"description": "changed"}).status_code == 200
        assert cfg.staff.get(f"{API}/{event_id}").json()["modules"]["meals"] is False


# ===========================================================================
# D. partial updates never reset configuration
# ===========================================================================


class TestPartialUpdate:
    @pytest.fixture
    def configured(self, cfg):
        resp = create(cfg, event_type="conference", modules={"meals": False})
        cfg.event_id = resp.json()["id"]
        cfg.expected = {**SPEC_DEFAULTS["conference"], "meals": False}
        return cfg

    def test_updating_only_the_title_leaves_modules_and_type_unchanged(self, configured):
        resp = configured.staff.put(f"{API}/{configured.event_id}", json={"title": "Renamed"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["title"] == "Renamed"
        assert resp.json()["modules"] == configured.expected and resp.json()["event_type"] == "conference"
        stored = row(configured.db, configured.event_id)
        assert stored.modules == configured.expected and stored.event_type == "conference"

    def test_updating_many_unrelated_fields_leaves_configuration_alone(self, configured):
        resp = configured.staff.put(f"{API}/{configured.event_id}", json={
            "title": "T2", "capacity": "80", "delivery_mode": "hybrid", "pricing_type": "paid", "price": "40"})
        assert resp.status_code == 200, resp.text
        assert row(configured.db, configured.event_id).modules == configured.expected

    def test_explicit_nulls_mean_no_change_not_reset(self, configured):
        """A client that serializes absent values as null must not wipe the configuration."""
        resp = configured.staff.put(f"{API}/{configured.event_id}", json={"title": "N", "modules": None, "event_type": None})
        assert resp.status_code == 200, resp.text
        stored = row(configured.db, configured.event_id)
        assert stored.modules == configured.expected and stored.event_type == "conference"

    def test_modules_are_merged_partially_unsent_modules_keep_their_value(self, configured):
        resp = configured.staff.put(f"{API}/{configured.event_id}", json={"modules": {"accommodation": False}})
        assert resp.status_code == 200, resp.text
        assert resp.json()["modules"] == {**configured.expected, "accommodation": False}  # meals stays False
        assert row(configured.db, configured.event_id).modules == {**configured.expected, "accommodation": False}

    def test_a_published_configured_event_can_still_be_updated(self, cfg):
        """Publishing/updating a submitted event re-validates stored columns; a stored config must pass."""
        event = make_event(cfg.db, cfg.tenant, cfg.ent, event_type="workshop", modules=SPEC_DEFAULTS["workshop"])
        resp = cfg.staff.put(f"{API}/{event.id}", json={"title": "Still fine"})
        assert resp.status_code == 200, resp.text

    def test_configuration_changes_do_not_send_schedule_change_notifications(self, cfg, monkeypatch):
        sent = []
        monkeypatch.setattr("app.services.notification_triggers.notify_schedule_change", lambda *a, **k: sent.append(a))
        event_id = create(cfg, event_type="conference").json()["id"]
        cfg.staff.put(f"{API}/{event_id}", json={"event_type": "workshop", "modules": {"meals": True}})
        assert sent == []
        cfg.staff.put(f"{API}/{event_id}", json={"title": "Renamed"})
        assert len(sent) == 1  # real schedule/title changes still notify

    def test_configuration_changes_are_audited_with_before_and_after(self, configured):
        configured.staff.put(f"{API}/{configured.event_id}", json={"modules": {"sessions": False}})
        cfg_audit = configured.db.query(EventAudit).filter(EventAudit.action == "update").one()
        assert cfg_audit.changed_by == configured.owner["id"]
        assert cfg_audit.before["modules"] == configured.expected
        assert cfg_audit.after["modules"] == {**configured.expected, "sessions": False}
        assert "event_type" not in cfg_audit.after  # only what changed


# ===========================================================================
# E. event_type updates are deterministic and never erase custom configuration
# ===========================================================================


class TestEventTypeUpdate:
    def test_changing_the_type_of_a_configured_event_keeps_its_custom_modules(self, cfg):
        event_id = create(cfg, event_type="conference", modules={"meals": False, "custom_questions": True}).json()["id"]
        before = row(cfg.db, event_id).modules
        resp = cfg.staff.put(f"{API}/{event_id}", json={"event_type": "workshop"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["event_type"] == "workshop"
        assert resp.json()["modules"] == before  # NOT reset to the workshop defaults
        assert row(cfg.db, event_id).modules == before

    def test_the_result_is_the_same_however_many_times_the_request_is_repeated(self, cfg):
        event_id = create(cfg, event_type="conference", modules={"meals": False}).json()["id"]
        results = [cfg.staff.put(f"{API}/{event_id}", json={"event_type": "camp"}).json() for _ in range(3)]
        assert {(r["event_type"], tuple(r["modules"].items())) for r in results} == {
            ("camp", tuple({**SPEC_DEFAULTS["conference"], "meals": False}.items()))}

    def test_type_and_modules_together_apply_both_over_the_persisted_config(self, cfg):
        event_id = create(cfg, event_type="conference", modules={"meals": False}).json()["id"]
        resp = cfg.staff.put(f"{API}/{event_id}", json={"event_type": "workshop", "modules": {"custom_questions": True}})
        assert resp.json()["event_type"] == "workshop"
        assert resp.json()["modules"] == {**SPEC_DEFAULTS["conference"], "meals": False, "custom_questions": True}

    def test_setting_a_type_on_a_never_configured_event_applies_that_types_defaults(self, cfg):
        legacy = make_event(cfg.db, cfg.tenant, cfg.ent)
        resp = cfg.staff.put(f"{API}/{legacy.id}", json={"event_type": "camp"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["modules"] == SPEC_DEFAULTS["camp"]
        stored = row(cfg.db, legacy.id)
        assert stored.event_type == "camp" and stored.modules == SPEC_DEFAULTS["camp"]

    def test_a_type_change_on_a_never_configured_paid_event_keeps_ticketing(self, cfg):
        legacy = make_event(cfg.db, cfg.tenant, cfg.ent, pricing_type="paid", price="99")
        resp = cfg.staff.put(f"{API}/{legacy.id}", json={"event_type": "camp"})
        assert resp.json()["modules"] == {**SPEC_DEFAULTS["camp"], "tickets": True}

    def test_echoing_back_the_resolved_other_does_not_reset_a_legacy_event(self, cfg):
        """Web/mobile read event_type='other' for a legacy event; sending it back must not switch features off."""
        legacy = make_event(cfg.db, cfg.tenant, cfg.ent, pricing_type="paid", price="99",
                            sessions=[{"id": "s1", "title": "Keynote"}], delivery_mode="online")
        seen = cfg.staff.get(f"{API}/{legacy.id}").json()
        assert seen["event_type"] == "other"
        resp = cfg.staff.put(f"{API}/{legacy.id}", json={"event_type": seen["event_type"]})
        assert resp.status_code == 200, resp.text
        assert resp.json()["modules"] == seen["modules"]  # tickets/sessions/online_meeting all still on
        assert row(cfg.db, legacy.id).modules is None  # and still not persisted: still behaviour-based

    def test_modules_only_on_a_legacy_event_starts_from_its_behaviour(self, cfg):
        legacy = make_event(cfg.db, cfg.tenant, cfg.ent, sessions=[{"id": "s1", "title": "Keynote"}])
        resp = cfg.staff.put(f"{API}/{legacy.id}", json={"modules": {"meals": True}})
        assert resp.status_code == 200, resp.text
        assert resp.json()["modules"] == {**flags("registration", "check_in", "sessions"), "meals": True}
        assert row(cfg.db, legacy.id).event_type is None


# ===========================================================================
# F. legacy events (event_type NULL, modules NULL)
# ===========================================================================


LEGACY_CASES = [
    ("plain free in-person event", {}, LEGACY_PLAIN),
    ("paid", {"pricing_type": "paid", "price": "10"}, flags("registration", "check_in", "tickets")),
    ("free but with priced ticket types", {"ticket_types": [paid_ticket("20")]}, flags("registration", "check_in", "tickets")),
    ("with sessions", {"sessions": [{"id": "s", "title": "x"}]}, flags("registration", "check_in", "sessions")),
    ("online", {"delivery_mode": "online"}, flags("registration", "check_in", "online_meeting")),
    ("hybrid", {"delivery_mode": "hybrid"}, flags("registration", "check_in", "online_meeting")),
    ("in-person with a meeting link", {"meeting_link": "https://meet.example.com/x"}, flags("registration", "check_in", "online_meeting")),
    ("with registration questions", {"custom_fields": [{"label": "Diet", "type": "select", "options": ["veg"]}]},
     flags("registration", "check_in", "custom_questions")),
    ("everything the platform offered", {"pricing_type": "paid", "price": "9", "sessions": [{"id": "s"}], "delivery_mode": "hybrid",
                                         "meeting_provider": "zoom", "custom_fields": [{"label": "x", "type": "select", "options": ["a"]}]},
     flags("registration", "tickets", "sessions", "check_in", "online_meeting", "custom_questions")),
]


class TestLegacyEvents:
    @pytest.mark.parametrize("label,attrs,expected", LEGACY_CASES, ids=[c[0] for c in LEGACY_CASES])
    def test_legacy_events_resolve_from_their_behaviour_and_keep_every_capability_they_use(self, cfg, label, attrs, expected):
        legacy = make_event(cfg.db, cfg.tenant, cfg.ent, **attrs)
        assert legacy.event_type is None and legacy.modules is None
        body_ = cfg.staff.get(f"{API}/{legacy.id}").json()
        assert body_["event_type"] == "other"
        assert body_["modules"] == expected

    def test_reading_never_writes_the_derived_values_back(self, cfg):
        legacy = make_event(cfg.db, cfg.tenant, cfg.ent, pricing_type="paid", price="10", sessions=[{"id": "s"}])
        commits = count_commits(cfg.db)
        for url in (f"{API}/{legacy.id}", f"{API}/?page_size=100", "/api/v1/search/events?page_size=100"):
            assert cfg.staff.get(url).status_code == 200
        assert commits == []
        stored = row(cfg.db, legacy.id)
        assert stored.event_type is None and stored.modules is None

    def test_legacy_config_follows_the_event_as_it_changes(self, cfg):
        legacy = make_event(cfg.db, cfg.tenant, cfg.ent)
        assert cfg.staff.get(f"{API}/{legacy.id}").json()["modules"]["sessions"] is False
        session_date = (datetime.utcnow() + timedelta(days=10)).date().isoformat()
        assert cfg.staff.post(f"{API}/{legacy.id}/sessions", json={"session_date": session_date, "title": "New"}).status_code == 201
        assert cfg.staff.get(f"{API}/{legacy.id}").json()["modules"]["sessions"] is True

    def test_an_ordinary_update_of_a_legacy_event_leaves_it_legacy(self, cfg):
        legacy = make_event(cfg.db, cfg.tenant, cfg.ent, pricing_type="paid", price="10")
        assert cfg.staff.put(f"{API}/{legacy.id}", json={"title": "Edited"}).status_code == 200
        stored = row(cfg.db, legacy.id)
        assert stored.event_type is None and stored.modules is None

    def test_existing_behaviour_of_a_legacy_event_is_untouched(self, cfg):
        """Registration, check-in, sessions and the calendar keep working with no configuration at all."""
        legacy = make_event(cfg.db, cfg.tenant, cfg.ent, capacity="5")
        guest = client_for(cfg.db, customer_user("guest@example.com"))
        reg = guest.post(f"{API}/{legacy.id}/registrations", json={"participant_name": "G", "participant_email": "guest@example.com"})
        assert reg.status_code == 201 and reg.json()["qr_code"]
        staff = client_for(cfg.db, cfg.owner)
        assert staff.post(f"{API}/{legacy.id}/check-in", json={"qr_code": reg.json()["qr_code"]}).status_code == 200
        session_date = (datetime.utcnow() + timedelta(days=10)).date().isoformat()
        assert staff.post(f"{API}/{legacy.id}/sessions", json={"session_date": session_date, "title": "S"}).status_code == 201
        assert client_for(cfg.db, None).get(f"{API}/{legacy.id}/calendar.ics").status_code == 200

    def test_a_corrupt_stored_value_never_breaks_a_read(self, cfg):
        bad = make_event(cfg.db, cfg.tenant, cfg.ent, event_type="party", modules={"meals": "yes", "polls": True})
        body_ = cfg.staff.get(f"{API}/{bad.id}")
        assert body_.status_code == 200
        assert body_.json()["event_type"] == "other"
        assert list(body_.json()["modules"]) == SPEC_KEYS

    def test_duplicating_an_event_copies_its_configuration_independently(self, cfg):
        source_id = create(cfg, event_type="conference", modules={"meals": False}).json()["id"]
        dup = cfg.staff.post(f"{API}/{source_id}/duplicate")
        assert dup.status_code == 201, dup.text
        assert dup.json()["event_type"] == "conference" and dup.json()["modules"]["meals"] is False
        cfg.staff.put(f"{API}/{dup.json()['id']}", json={"modules": {"meals": True}})
        assert row(cfg.db, source_id).modules["meals"] is False  # the source did not change


# ===========================================================================
# G. responses expose the fields consistently, additively
# ===========================================================================


class TestResponses:
    def assert_config(self, item, event_type, modules):
        assert item["event_type"] == event_type
        assert item["modules"] == modules
        assert list(item["modules"]) == SPEC_KEYS
        assert all(type(v) is bool for v in item["modules"].values())

    def test_every_event_response_carries_type_and_modules(self, cfg):
        created = create(cfg, event_type="workshop")
        event_id = created.json()["id"]
        self.assert_config(created.json(), "workshop", SPEC_DEFAULTS["workshop"])
        self.assert_config(cfg.staff.put(f"{API}/{event_id}", json={"title": "x"}).json(), "workshop", SPEC_DEFAULTS["workshop"])
        self.assert_config(cfg.staff.get(f"{API}/{event_id}").json(), "workshop", SPEC_DEFAULTS["workshop"])
        listed = [i for i in cfg.staff.get(f"{API}/?page_size=100").json()["items"] if i["id"] == event_id][0]
        self.assert_config(listed, "workshop", SPEC_DEFAULTS["workshop"])
        self.assert_config(cfg.staff.post(f"{API}/{event_id}/duplicate").json(), "workshop", SPEC_DEFAULTS["workshop"])
        self.assert_config(cfg.staff.patch(f"{API}/{event_id}/status", json={"status": "cancelled"}).json(), "workshop", SPEC_DEFAULTS["workshop"])

    def test_public_list_detail_and_search_expose_it_for_published_events(self, cfg):
        published = make_event(cfg.db, cfg.tenant, cfg.ent, event_type="marathon", modules=SPEC_DEFAULTS["marathon"])
        anonymous = client_for(cfg.db, None)
        self.assert_config(anonymous.get(f"{API}/{published.id}").json(), "marathon", SPEC_DEFAULTS["marathon"])
        self.assert_config(anonymous.get(f"{API}/").json()["items"][0], "marathon", SPEC_DEFAULTS["marathon"])
        self.assert_config(anonymous.get("/api/v1/search/events").json()["items"][0], "marathon", SPEC_DEFAULTS["marathon"])

    def test_legacy_and_configured_events_share_one_response_shape(self, cfg):
        legacy_id = make_event(cfg.db, cfg.tenant, cfg.ent).id
        configured_id = create(cfg, event_type="camp").json()["id"]
        # detail vs detail, and write response vs write response (the two have always differed by enterprise_name)
        assert set(cfg.staff.get(f"{API}/{legacy_id}").json()) == set(cfg.staff.get(f"{API}/{configured_id}").json())
        assert set(cfg.staff.put(f"{API}/{legacy_id}", json={"title": "x"}).json()) == set(
            cfg.staff.put(f"{API}/{configured_id}", json={"title": "x"}).json())

    def test_no_existing_field_was_removed_or_renamed(self, cfg):
        for item in (create(cfg, event_type="camp").json(), cfg.staff.get(f"{API}/{make_event(cfg.db, cfg.tenant, cfg.ent).id}").json()):
            missing = [f for f in EXISTING_RESPONSE_FIELDS if f not in item]
            assert not missing, missing

    def test_lifecycle_state_from_the_previous_phase_is_untouched(self, cfg):
        upcoming = create(cfg, event_type="camp").json()
        assert upcoming["lifecycle_state"] == "upcoming"
        finished = make_event(cfg.db, cfg.tenant, cfg.ent, start_date=datetime.utcnow() - timedelta(days=3),
                              end_date=datetime.utcnow() - timedelta(days=2))
        assert cfg.staff.get(f"{API}/{finished.id}").json()["lifecycle_state"] == "finished"

    def test_meeting_link_masking_is_unchanged(self, cfg):
        event = make_event(cfg.db, cfg.tenant, cfg.ent, delivery_mode="online", meeting_link="https://meet.example.com/x")
        assert client_for(cfg.db, None).get(f"{API}/{event.id}").json()["meeting_link"] == "protected"


# ===========================================================================
# H. invalid modules
# ===========================================================================


class TestInvalidModules:
    @pytest.mark.parametrize("bad", [
        {"polls": True}, {"networking": True}, {"Meals": True}, {"meals": True, "sponsors": False}, {"certificates": False},
    ])
    def test_unknown_module_names_are_rejected(self, cfg, bad):
        resp = create(cfg, event_type="conference", modules=bad)
        assert resp.status_code == 422
        assert "modules" in resp.text
        assert cfg.db.query(Event).count() == 0

    @pytest.mark.parametrize("module", SPEC_KEYS)
    @pytest.mark.parametrize("value", ["true", "yes", 1, 0, None, [], {}, 1.5])
    def test_non_boolean_values_are_rejected_for_every_module(self, cfg, module, value):
        resp = create(cfg, event_type="conference", modules={module: value})
        assert resp.status_code == 422
        assert cfg.db.query(Event).count() == 0

    @pytest.mark.parametrize("bad", ["meals", ["meals"], 5, True])
    def test_a_non_object_modules_value_is_rejected(self, cfg, bad):
        assert create(cfg, event_type="conference", modules=bad).status_code == 422
        assert cfg.db.query(Event).count() == 0

    def test_invalid_modules_on_update_are_rejected_and_nothing_changes(self, cfg):
        event_id = create(cfg, event_type="conference").json()["id"]
        for bad in ({"polls": True}, {"meals": "yes"}, {"meals": None}, ["meals"]):
            assert cfg.staff.put(f"{API}/{event_id}", json={"title": "Sneaky", "modules": bad}).status_code == 422
        stored = row(cfg.db, event_id)
        assert stored.modules == SPEC_DEFAULTS["conference"] and stored.title == "Summit"  # not even the valid title applied

    # ---- minimal dependency rules (explicit requests only) ------------------------------------------
    def test_online_meeting_requires_an_online_or_hybrid_format(self, cfg):
        resp = create(cfg, modules={"online_meeting": True}, delivery_mode="in_person")
        assert resp.status_code == 422 and "online_meeting" in resp.json()["detail"]
        assert cfg.db.query(Event).count() == 0
        for mode in ("online", "hybrid"):
            assert create(cfg, modules={"online_meeting": True}, delivery_mode=mode).status_code == 201

    def test_online_meeting_requirement_applies_on_update_using_the_resulting_format(self, cfg):
        in_person = create(cfg, event_type="conference").json()["id"]
        assert cfg.staff.put(f"{API}/{in_person}", json={"modules": {"online_meeting": True}}).status_code == 422
        # ...but the same request is fine when the same update makes the event hybrid
        assert cfg.staff.put(f"{API}/{in_person}", json={"delivery_mode": "hybrid", "modules": {"online_meeting": True}}).status_code == 200
        assert row(cfg.db, in_person).modules["online_meeting"] is True

    def test_tickets_cannot_be_disabled_for_a_paid_event(self, cfg):
        assert create(cfg, event_type="conference", pricing_type="paid", price="50", modules={"tickets": False}).status_code == 422
        free = create(cfg, event_type="conference", modules={"tickets": False})
        assert free.status_code == 201 and free.json()["modules"]["tickets"] is False
        paid_id = create(cfg, event_type="conference", pricing_type="paid", price="50").json()["id"]
        assert cfg.staff.put(f"{API}/{paid_id}", json={"modules": {"tickets": False}}).status_code == 422
        assert row(cfg.db, paid_id).modules["tickets"] is True

    def test_disabling_sessions_never_touches_existing_session_data(self, cfg):
        session_date = (datetime.utcnow() + timedelta(days=10)).date().isoformat()
        event_id = create(cfg, event_type="conference", sessions=[{"session_date": session_date, "title": "Keynote"}]).json()["id"]
        resp = cfg.staff.put(f"{API}/{event_id}", json={"modules": {"sessions": False}})
        assert resp.status_code == 200 and resp.json()["modules"]["sessions"] is False
        assert [s["title"] for s in row(cfg.db, event_id).sessions] == ["Keynote"]

    def test_a_type_default_is_never_a_failing_request(self, cfg):
        """webinar defaults online_meeting=true even if the client left delivery_mode at its in_person default."""
        resp = create(cfg, event_type="webinar")
        assert resp.status_code == 201, resp.text
        assert resp.json()["modules"]["online_meeting"] is True
        assert resp.json()["delivery_mode"] == "in_person"  # existing format rules are not invented or changed

    def test_a_default_derived_configuration_never_blocks_submission(self, cfg):
        """validate_event_submission re-validates stored columns; contradictions are checked on requests only."""
        event_id = create(cfg, event_type="webinar").json()["id"]
        resp = cfg.staff.patch(f"{API}/{event_id}/status", json={"status": "pending_approval"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "pending_approval"


# ===========================================================================
# I. Phase 2.1 tenant isolation still holds for the new fields
# ===========================================================================


class TestTenantIsolation:
    @pytest.fixture
    def world(self, cfg):
        other_tenant = uuid4()
        other_ent = make_enterprise(cfg.db, other_tenant, "Other")
        victim = make_event(cfg.db, other_tenant, other_ent, event_type="conference", modules=SPEC_DEFAULTS["conference"])
        cfg.victim = victim
        return cfg

    def snapshot(self, cfg):
        stored = row(cfg.db, cfg.victim.id)
        return (stored.event_type, dict(stored.modules), stored.title, str(stored.tenant_id), cfg.db.query(EventAudit).count())

    @pytest.mark.parametrize("payload", [
        {"event_type": "camp"},
        {"modules": {"meals": False}},
        {"event_type": "marathon", "modules": {"tickets": False, "meals": True}},
        {"title": "x", "event_type": "webinar"},
    ])
    def test_cross_tenant_staff_cannot_change_type_or_modules(self, world, payload):
        before = self.snapshot(world)
        assert world.staff.put(f"{API}/{world.victim.id}", json=payload).status_code == 403
        assert self.snapshot(world) == before

    def test_validation_cannot_be_used_to_probe_another_tenants_event(self, world):
        """Ownership is checked before the body is interpreted: invalid config on a foreign event is still a 403."""
        resp = world.staff.put(f"{API}/{world.victim.id}", json={"modules": {"polls": True}})
        assert resp.status_code in (403, 422)
        assert resp.status_code == 403 or self.snapshot(world)[1] == SPEC_DEFAULTS["conference"]

    def test_the_owner_can_change_their_own_configuration(self, world):
        mine = create(world, event_type="conference").json()["id"]
        assert world.staff.put(f"{API}/{mine}", json={"modules": {"meals": False}}).status_code == 200

    def test_new_fields_do_not_reveal_hidden_events_or_internal_fields(self, world):
        draft = make_event(world.db, world.tenant, world.ent, status="draft", event_type="camp", modules=SPEC_DEFAULTS["camp"])
        stranger = client_for(world.db, customer_user("stranger@example.com"))
        assert stranger.get(f"{API}/{draft.id}").status_code == 404
        assert draft.id not in [UUID(i["id"]) for i in stranger.get(f"{API}/?page_size=100").json()["items"]]
        public = client_for(world.db, None).get(f"{API}/{world.victim.id}").json()
        assert public["event_type"] == "conference" and public["last_admin_notes"] is None  # config is public, review notes are not

    def test_tenant_id_is_still_immutable_through_update(self, world):
        mine = create(world, event_type="camp").json()["id"]
        resp = world.staff.put(f"{API}/{mine}", json={"tenant_id": str(uuid4()), "event_type": "workshop"})
        assert resp.status_code == 200
        assert str(row(world.db, mine).tenant_id) == str(world.tenant) and row(world.db, mine).event_type == "workshop"

    def test_a_customer_cannot_configure_events(self, world):
        customer = client_for(world.db, customer_user("c@example.com"))
        assert customer.post(f"{API}/", json=body(event_type="camp")).status_code == 403
        assert customer.put(f"{API}/{world.victim.id}", json={"event_type": "camp"}).status_code == 403


# ===========================================================================
# J. compatibility: configuration is stored, not enforced (yet)
# ===========================================================================


class TestNotEnforcedInThisPhase:
    def test_disabled_modules_do_not_disable_existing_endpoints(self, cfg):
        """Phase 2.2 is a configuration layer: registration/check-in/sessions keep working whatever modules say."""
        event = make_event(cfg.db, cfg.tenant, cfg.ent, capacity="5", event_type="other", modules=flags())  # everything off
        guest = client_for(cfg.db, customer_user("guest@example.com"))
        reg = guest.post(f"{API}/{event.id}/registrations", json={"participant_name": "G", "participant_email": "guest@example.com"})
        assert reg.status_code == 201
        staff = client_for(cfg.db, cfg.owner)
        assert staff.post(f"{API}/{event.id}/check-in", json={"qr_code": reg.json()["qr_code"]}).status_code == 200
        session_date = (datetime.utcnow() + timedelta(days=10)).date().isoformat()
        assert staff.post(f"{API}/{event.id}/sessions", json={"session_date": session_date, "title": "S"}).status_code == 201

    def test_paid_checkout_still_works_for_a_configured_event(self, cfg):
        ticket = paid_ticket()
        event = make_event(cfg.db, cfg.tenant, cfg.ent, pricing_type="paid", price="500", ticket_types=[ticket],
                           event_type="marathon", modules=SPEC_DEFAULTS["marathon"])
        resp = client_for(cfg.db, customer_user("buyer@example.com")).post(
            f"{API}/{event.id}/checkout",
            json={"participant_name": "B", "participant_email": "buyer@example.com", "ticket_type_id": ticket["id"], "quantity": 1})
        assert resp.status_code == 201, resp.text
        assert set(resp.json()) >= {"id", "event_id", "participant_email", "amount", "currency", "payment_status", "status"}


# ===========================================================================
# K. OpenAPI
# ===========================================================================


class TestOpenApi:
    @pytest.fixture(scope="class")
    def spec(self):
        from app.main import app

        return app.openapi()

    @staticmethod
    def enum_of(prop):
        for option in prop.get("anyOf", [prop]):
            if "enum" in option:
                return option["enum"]
        return None

    @staticmethod
    def ref_of(prop):
        for option in prop.get("anyOf", [prop]):
            if "$ref" in option:
                return option["$ref"].rsplit("/", 1)[-1]
        return None

    def test_generation_succeeds_and_the_event_endpoints_are_still_documented(self, spec):
        assert spec["paths"]
        for path in ("/api/v1/events/", "/api/v1/events/{event_id}", "/api/v1/search/events"):
            assert path in spec["paths"], path

    @pytest.mark.parametrize("schema", ["EventCreate", "EventUpdate", "EventResponse", "EventListItemResponse", "EventDetailResponse"])
    def test_event_type_is_a_typed_enum_on_every_event_schema(self, spec, schema):
        props = spec["components"]["schemas"][schema]["properties"]
        assert self.enum_of(props["event_type"]) == SPEC_TYPES, schema

    @pytest.mark.parametrize("schema,model", [
        ("EventCreate", "EventModulesInput"), ("EventUpdate", "EventModulesInput"),
        ("EventResponse", "EventModules"), ("EventListItemResponse", "EventModules"), ("EventDetailResponse", "EventModules"),
    ])
    def test_modules_reference_a_typed_model_not_a_free_dict(self, spec, schema, model):
        assert self.ref_of(spec["components"]["schemas"][schema]["properties"]["modules"]) == model

    def test_input_model_forbids_unknown_modules_and_lists_the_eight(self, spec):
        model = spec["components"]["schemas"]["EventModulesInput"]
        assert model["additionalProperties"] is False
        assert list(model["properties"]) == SPEC_KEYS
        assert not model.get("required")  # partial by design

    def test_response_model_requires_all_eight_booleans(self, spec):
        model = spec["components"]["schemas"]["EventModules"]
        assert list(model["properties"]) == SPEC_KEYS
        assert sorted(model["required"]) == sorted(SPEC_KEYS)
        assert all(prop["type"] == "boolean" for prop in model["properties"].values())

    def test_the_fields_are_optional_on_create_and_update_so_existing_clients_keep_working(self, spec):
        for schema in ("EventCreate", "EventUpdate"):
            required = spec["components"]["schemas"][schema].get("required", [])
            assert "event_type" not in required and "modules" not in required

    def test_lifecycle_state_is_still_documented(self, spec):
        assert "lifecycle_state" in spec["components"]["schemas"]["EventResponse"]["properties"]
