"""
Event Management Phase 2.3 — attendee management.

Real (in-memory SQLite) database via tests/event_sql_support.py, so search, filters, the order pairing
behind ``payment_status``, pagination and the CSV export run as SQL.

Covers: the typed attendee list, ``q`` search (name / email / reference / id, case-insensitive, wildcard-safe),
status / ticket type / payment status / checked-in / date filters, pagination + deterministic ordering,
attendee detail (cross-event protection), custom answers through the Event Form infrastructure, the CSV
export (filters, formula-injection, security), tenant/ownership, and the untouched legacy list.

Run:
    pytest tests/test_event_phase_2_3_attendees.py -v
"""
import csv
import io
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event as sa_event

from event_sql_support import (
    API,
    EventRegistration,
    client_for,
    customer_user,
    make_enterprise,
    make_event,
    make_order,
    make_registration,
    make_session,
    paid_ticket,
    reset_overrides,
    silence_side_effects,
    staff_user,
    super_admin_user,
)
from app.models.event_form_config_model import EventFormConfiguration, EventFormConfigurationVersion
from app.services.event_form_registry import LEGACY_VERSION_ID

ATTENDEE_KEYS = {
    "registration_id", "event_id", "participant_name", "participant_email", "registration_status",
    "registration_reference", "ticket_type_id", "ticket_type_name", "quantity", "payment_status", "order_id",
    "order_status", "amount", "currency", "is_checked_in", "checked_in_at", "checked_out_at", "registered_at",
    "custom_answers",
}
# Fields that exist on the ORM rows but must never reach an attendee response.
HIDDEN_KEYS = {
    "id", "status", "qr_code", "custom_fields", "checked_in_by", "session_id", "payment_provider", "refund_reason",
    "tenant_id", "enterprise_id", "meeting_link", "updated_at", "created_at",
}


@pytest.fixture
def env(monkeypatch):
    silence_side_effects(monkeypatch)
    db = make_session()
    tenant = uuid4()
    ent = make_enterprise(db, tenant)
    ticket = paid_ticket("500", ticket_id="general")
    event = make_event(db, tenant, ent, pricing_type="paid", price="500", ticket_types=[ticket], capacity="500")
    yield SimpleNamespace(db=db, tenant=tenant, ent=ent, event=event, ticket=ticket, owner=staff_user(tenant, "admin"))
    reset_overrides()
    db.close()


def owner_client(env):
    return client_for(env.db, env.owner)


def add_reg(env, email, name=None, event=None, **kwargs):
    reg = make_registration(env.db, event or env.event, email, **kwargs)
    if name:
        reg.participant_name = name
        env.db.commit()
    return reg


def attendees(env, event=None, **params):
    resp = owner_client(env).get(f"{API}/{(event or env.event).id}/attendees", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def emails(body):
    return [item["participant_email"] for item in body["items"]]


# ===========================================================================
# 1-3. Typed response, no leaks
# ===========================================================================


class TestAttendeeResponse:
    def test_paid_attendee_has_the_documented_fields(self, env):
        client = client_for(env.db, customer_user("buyer@example.com"))
        resp = client.post(f"{API}/{env.event.id}/checkout", json={
            "participant_name": "Buyer", "participant_email": "buyer@example.com",
            "ticket_type_id": "general", "quantity": 2})
        assert resp.status_code == 201, resp.text
        order_id = resp.json()["id"]

        item = attendees(env)["items"][0]
        assert set(item) == ATTENDEE_KEYS
        assert item["participant_name"] == "Buyer" and item["participant_email"] == "buyer@example.com"
        assert item["registration_status"] == "confirmed" and item["registration_reference"]
        assert item["ticket_type_id"] == "general" and item["ticket_type_name"] == "General"
        assert item["quantity"] == 2 and item["payment_status"] == "paid"
        assert item["order_id"] == order_id and item["order_status"] == "confirmed"
        assert item["amount"] == 1000.0 and item["currency"] == "INR"  # the order total: 2 x 500
        assert item["is_checked_in"] is False and item["checked_in_at"] is None
        assert item["event_id"] == str(env.event.id) and UUID(item["registration_id"])
        assert item["custom_answers"] == []

    def test_free_event_attendee_is_free_without_an_order(self, env):
        free = make_event(env.db, env.tenant, env.ent)
        add_reg(env, "free@example.com", event=free)
        item = attendees(env, free)["items"][0]
        assert item["payment_status"] == "free" and item["order_id"] is None
        assert item["amount"] is None and item["quantity"] == 1

    def test_paid_event_registration_without_an_order_is_unpaid(self, env):
        add_reg(env, "nopay@example.com")
        assert attendees(env)["items"][0]["payment_status"] == "unpaid"

    def test_no_orm_internals_or_payment_secrets_are_exposed(self, env):
        add_reg(env, "a@example.com", custom_fields={"q": "a"})
        make_order(env.db, env.event, "a@example.com")
        env.db.query(EventRegistration).update({"checked_in_by": uuid4(), "session_id": "s1"})
        env.db.commit()
        item = attendees(env)["items"][0]
        assert not (set(item) & HIDDEN_KEYS)
        body = str(attendees(env))
        for secret in ("marketplace", "tenant_id", "meeting_link"):  # payment_provider value / tenant internals
            assert secret not in body

    def test_checked_in_registration_reports_time_and_flag(self, env):
        when = datetime(2026, 3, 1, 10, 30)
        add_reg(env, "in@example.com", status="attended", checked_in_at=when)
        add_reg(env, "out@example.com")
        by_email = {i["participant_email"]: i for i in attendees(env)["items"]}
        assert by_email["in@example.com"]["is_checked_in"] is True
        assert by_email["in@example.com"]["checked_in_at"].startswith("2026-03-01T10:30")
        assert by_email["out@example.com"]["is_checked_in"] is False

    def test_unknown_ticket_type_resolves_to_null_name_not_an_error(self, env):
        add_reg(env, "old@example.com", ticket_type_id="deleted-ticket")
        item = attendees(env)["items"][0]
        assert item["ticket_type_id"] == "deleted-ticket" and item["ticket_type_name"] is None


# ===========================================================================
# 4-9. Search + filters
# ===========================================================================


class TestSearch:
    @pytest.fixture(autouse=True)
    def people(self, env):
        self.alice = add_reg(env, "alice@corp.com", name="Alice Wonder")
        self.bob = add_reg(env, "bob.smith@example.com", name="Bob Smith")
        self.carol = add_reg(env, "carol@example.com", name="Carol_100%")

    def test_q_matches_name_case_insensitively(self, env):
        assert emails(attendees(env, q="wONDER")) == ["alice@corp.com"]

    def test_q_matches_email(self, env):
        assert emails(attendees(env, q="BOB.SMITH")) == ["bob.smith@example.com"]

    def test_q_matches_registration_reference(self, env):
        assert emails(attendees(env, q=self.bob.qr_code.lower())) == ["bob.smith@example.com"]

    def test_q_matches_a_pasted_registration_id_exactly(self, env):
        assert emails(attendees(env, q=str(self.carol.id))) == ["carol@example.com"]

    def test_q_is_trimmed_and_a_blank_q_means_no_search(self, env):
        assert len(attendees(env, q="   ")["items"]) == 3
        assert emails(attendees(env, q="  alice ")) == ["alice@corp.com"]

    def test_like_wildcards_in_q_are_literal(self, env):
        assert attendees(env, q="%")["pagination"]["total"] == 1  # only "Carol_100%" contains a real %
        assert emails(attendees(env, q="_100%")) == ["carol@example.com"]
        assert attendees(env, q="a_c")["items"] == []  # "_" must not match any character
        assert attendees(env, q="\\")["items"] == []

    def test_no_match_is_an_empty_page_not_an_error(self, env):
        body = attendees(env, q="zzz-nobody")
        assert body["items"] == [] and body["pagination"]["total"] == 0 and body["pagination"]["total_pages"] == 0

    def test_search_never_crosses_events(self, env):
        other = make_event(env.db, env.tenant, env.ent)
        add_reg(env, "alice-clone@corp.com", event=other)
        assert emails(attendees(env, q="alice")) == ["alice@corp.com"]
        assert emails(attendees(env, other, q="alice")) == ["alice-clone@corp.com"]


class TestFilters:
    def test_status_filter(self, env):
        add_reg(env, "c@example.com")
        add_reg(env, "a@example.com", status="attended")
        add_reg(env, "x@example.com", status="cancelled")
        add_reg(env, "n@example.com", status="no_show")
        for status, expected in (("confirmed", "c@example.com"), ("attended", "a@example.com"),
                                 ("cancelled", "x@example.com"), ("no_show", "n@example.com")):
            assert emails(attendees(env, status=status)) == [expected]

    def test_unknown_status_is_rejected_by_validation(self, env):
        resp = owner_client(env).get(f"{API}/{env.event.id}/attendees", params={"status": "bogus"})
        assert resp.status_code == 422

    def test_ticket_type_filter(self, env):
        add_reg(env, "g@example.com", ticket_type_id="general")
        add_reg(env, "v@example.com", ticket_type_id="vip")
        add_reg(env, "n@example.com")
        assert emails(attendees(env, ticket_type_id="vip")) == ["v@example.com"]
        assert attendees(env, ticket_type_id="nope")["pagination"]["total"] == 0

    def test_checked_in_filter_true_and_false(self, env):
        add_reg(env, "in@example.com", status="attended")
        add_reg(env, "c@example.com")
        add_reg(env, "x@example.com", status="cancelled")
        assert emails(attendees(env, checked_in="true")) == ["in@example.com"]
        assert sorted(emails(attendees(env, checked_in="false"))) == ["c@example.com", "x@example.com"]
        assert attendees(env)["pagination"]["total"] == 3  # absent = both

    def test_payment_status_filter_uses_the_paired_order(self, env):
        for email, order_status, payment in (
            ("paid@example.com", "confirmed", "confirmed"),
            ("refunded@example.com", "refunded", "refunded"),
            ("requested@example.com", "refund_requested", "refund_requested"),
            ("cancelled@example.com", "cancelled", "confirmed"),
        ):
            add_reg(env, email)
            make_order(env.db, env.event, email, status=order_status, payment_status=payment,
                       created_at=datetime(2020, 1, 1))
        add_reg(env, "unpaid@example.com")
        for wanted, expected in (("paid", "paid@example.com"), ("refunded", "refunded@example.com"),
                                 ("refund_requested", "requested@example.com"), ("cancelled", "cancelled@example.com"),
                                 ("unpaid", "unpaid@example.com")):
            assert emails(attendees(env, payment_status=wanted)) == [expected], wanted
        assert attendees(env, payment_status="free")["items"] == []  # a paid event has no "free" attendees
        assert attendees(env, payment_status="paid")["pagination"]["total"] == 1

    def test_payment_status_is_derived_and_never_stored_on_the_registration(self, env):
        assert "payment_status" not in EventRegistration.__table__.columns

    def test_free_event_payment_filter(self, env):
        free = make_event(env.db, env.tenant, env.ent)
        add_reg(env, "f@example.com", event=free)
        assert emails(attendees(env, free, payment_status="free")) == ["f@example.com"]
        assert attendees(env, free, payment_status="paid")["items"] == []

    def test_an_order_of_another_person_or_event_never_marks_an_attendee_paid(self, env):
        other = make_event(env.db, env.tenant, env.ent, pricing_type="paid", price="10")
        add_reg(env, "solo@example.com")
        # Both orders pre-date the registration, so only the event / email mismatch keeps them from pairing.
        make_order(env.db, other, "solo@example.com", created_at=datetime(2020, 1, 1))  # other event
        make_order(env.db, env.event, "someone.else@example.com", created_at=datetime(2020, 1, 1))  # other person
        assert attendees(env)["items"][0]["payment_status"] == "unpaid"

    def test_an_order_placed_after_the_registration_does_not_pair_with_it(self, env):
        add_reg(env, "late@example.com", created_at=datetime(2026, 1, 1))
        make_order(env.db, env.event, "late@example.com", created_at=datetime(2026, 6, 1))
        assert attendees(env)["items"][0]["payment_status"] == "unpaid"

    def test_registration_date_range_is_inclusive_of_whole_days(self, env):
        add_reg(env, "d10@example.com", created_at=datetime(2026, 1, 10, 0, 0, 0))
        add_reg(env, "d15@example.com", created_at=datetime(2026, 1, 15, 23, 59, 59))
        add_reg(env, "d20@example.com", created_at=datetime(2026, 1, 20, 12, 0, 0))
        assert emails(attendees(env, registered_from="2026-01-15", registered_to="2026-01-15")) == ["d15@example.com"]
        assert sorted(emails(attendees(env, registered_from="2026-01-15"))) == ["d15@example.com", "d20@example.com"]
        assert sorted(emails(attendees(env, registered_to="2026-01-15"))) == ["d10@example.com", "d15@example.com"]

    def test_inverted_or_malformed_date_range_is_a_422(self, env):
        client = owner_client(env)
        url = f"{API}/{env.event.id}/attendees"
        assert client.get(url, params={"registered_from": "2026-02-01", "registered_to": "2026-01-01"}).status_code == 422
        assert client.get(url, params={"registered_from": "not-a-date"}).status_code == 422

    def test_filters_combine_with_and(self, env):
        add_reg(env, "vip.in@example.com", name="Vip In", ticket_type_id="vip", status="attended")
        add_reg(env, "vip.out@example.com", name="Vip Out", ticket_type_id="vip")
        add_reg(env, "gen.in@example.com", name="Gen In", ticket_type_id="general", status="attended")
        assert emails(attendees(env, ticket_type_id="vip", checked_in="true")) == ["vip.in@example.com"]
        assert emails(attendees(env, q="in", ticket_type_id="general", status="attended")) == ["gen.in@example.com"]


# ===========================================================================
# 10. Pagination + ordering
# ===========================================================================


class TestPagination:
    @pytest.fixture
    def crowd(self, env):
        same_time = datetime(2026, 5, 1, 9, 0, 0)  # identical timestamps: only the id tie-breaker keeps pages stable
        env.db.add_all([
            EventRegistration(
                event_id=env.event.id, participant_name=f"Person {i:03d}", participant_email=f"p{i:03d}@example.com",
                status="confirmed", qr_code=f"REF{i:05d}", created_at=same_time)
            for i in range(130)
        ])
        env.db.commit()

    def test_metadata_matches_project_convention(self, env, crowd):
        body = attendees(env, page=2, page_size=50)
        assert body["pagination"] == {"total": 130, "page": 2, "page_size": 50, "total_pages": 3}
        assert len(body["items"]) == 50
        assert len(attendees(env, page=3, page_size=50)["items"]) == 30
        assert attendees(env, page=9, page_size=50)["items"] == []
        default = attendees(env)["pagination"]
        assert default["page"] == 1 and default["page_size"] == 20 and default["total_pages"] == 7

    @pytest.mark.parametrize("sort", ["newest", "oldest", "name", "email"])
    def test_pages_never_repeat_or_skip_rows(self, env, crowd, sort):
        walked = []
        for page in range(1, 6):
            walked += [i["registration_id"] for i in attendees(env, sort=sort, page=page, page_size=30)["items"]]
        assert len(walked) == 130 and len(set(walked)) == 130
        again = []
        for page in range(1, 3):
            again += [i["registration_id"] for i in attendees(env, sort=sort, page=page, page_size=100)["items"]]
        assert again == walked  # same order whatever the page size

    @pytest.mark.parametrize("sort, descending", [("newest", True), ("oldest", False), ("name", False), ("email", False)])
    def test_ties_are_broken_by_registration_id(self, env, sort, descending):
        """Identical sort keys must still come back in one fixed order (the id), not in whatever order the
        database happens to scan: that is what keeps pages stable on Postgres."""
        same_time = datetime(2026, 5, 1, 9, 0, 0)
        env.db.add_all([
            EventRegistration(event_id=env.event.id, participant_name="Same", participant_email="same@example.com",
                              status="cancelled", qr_code=f"TIE{i:04d}", created_at=same_time)
            for i in range(25)
        ])
        env.db.commit()
        expected = [str(r.id) for r in sorted(env.db.query(EventRegistration).all(), key=lambda r: r.id, reverse=descending)]
        got = [i["registration_id"] for i in attendees(env, sort=sort, page_size=100)["items"]]
        assert got == expected

    def test_sort_name_and_email_are_case_insensitive_and_ascending(self, env):
        add_reg(env, "z@example.com", name="zed")
        add_reg(env, "a@example.com", name="Alpha")
        add_reg(env, "m@example.com", name="mike")
        assert [i["participant_name"] for i in attendees(env, sort="name")["items"]] == ["Alpha", "mike", "zed"]
        assert emails(attendees(env, sort="email")) == ["a@example.com", "m@example.com", "z@example.com"]

    def test_default_sort_is_newest_first_and_oldest_reverses_it(self, env):
        add_reg(env, "old@example.com", created_at=datetime(2026, 1, 1))
        add_reg(env, "new@example.com", created_at=datetime(2026, 2, 1))
        assert emails(attendees(env)) == ["new@example.com", "old@example.com"]
        assert emails(attendees(env, sort="oldest")) == ["old@example.com", "new@example.com"]

    @pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 0}, {"page_size": 101}, {"sort": "random"}])
    def test_out_of_range_parameters_are_rejected(self, env, params):
        assert owner_client(env).get(f"{API}/{env.event.id}/attendees", params=params).status_code == 422

    def test_total_counts_matches_not_the_page(self, env, crowd):
        body = attendees(env, q="Person 1", page_size=10)  # "Person 100" .. "Person 129"
        assert body["pagination"]["total"] == 30 and body["pagination"]["total_pages"] == 3
        assert len(body["items"]) == 10


# ===========================================================================
# 11. Attendee detail
# ===========================================================================


class TestAttendeeDetail:
    def test_detail_matches_the_list_item(self, env):
        reg = add_reg(env, "d@example.com", name="Dee", ticket_type_id="general", custom_fields={"k": "v"})
        make_order(env.db, env.event, "d@example.com", quantity="3", amount="1500", created_at=datetime(2020, 1, 1))
        resp = owner_client(env).get(f"{API}/{env.event.id}/registrations/{reg.id}")
        assert resp.status_code == 200, resp.text
        detail = resp.json()
        assert detail == attendees(env)["items"][0]
        assert detail["quantity"] == 3 and detail["amount"] == 1500.0 and detail["payment_status"] == "paid"

    def test_a_registration_of_another_event_is_a_404_even_in_the_same_tenant(self, env):
        other = make_event(env.db, env.tenant, env.ent)
        foreign = add_reg(env, "elsewhere@example.com", event=other)
        resp = owner_client(env).get(f"{API}/{env.event.id}/registrations/{foreign.id}")
        assert resp.status_code == 404
        assert "elsewhere" not in resp.text

    def test_unknown_registration_and_unknown_event_are_404(self, env):
        client = owner_client(env)
        assert client.get(f"{API}/{env.event.id}/registrations/{uuid4()}").status_code == 404
        assert client.get(f"{API}/{uuid4()}/registrations/{uuid4()}").status_code == 404

    def test_detail_path_does_not_shadow_the_export_route(self, env):
        add_reg(env, "e@example.com")
        resp = owner_client(env).get(f"{API}/{env.event.id}/registrations/export")
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/csv")

    def test_malformed_registration_id_is_a_422(self, env):
        assert owner_client(env).get(f"{API}/{env.event.id}/registrations/not-a-uuid").status_code == 422


# ===========================================================================
# 12. Custom answers (existing Event Form infrastructure)
# ===========================================================================


def add_form_version(env, sections, version_id=None):
    config = EventFormConfiguration(name="Reg form", scope="global", status="published", is_active=True)
    env.db.add(config)
    env.db.commit()
    version = EventFormConfigurationVersion(
        id=UUID(version_id) if version_id else uuid4(), configuration_id=config.id, version=1, status="published", sections=sections)
    env.db.add(version)
    env.db.commit()
    return version


class TestCustomAnswers:
    def test_answers_carry_the_question_label_from_the_pinned_form_version(self, env):
        version = add_form_version(env, [{"id": "s1", "fields": [
            {"id": "f-size", "label": "T-shirt size", "source": "custom", "is_enabled": True},
            {"id": "f-diet", "label": "Diet", "source": "custom", "is_enabled": False},  # disabled later: still readable
        ]}])
        env.event.form_configuration_version_id = version.id
        env.db.commit()
        add_reg(env, "a@example.com", custom_fields={
            "f-size": "XL", "f-diet": ["vegan", "nut-free"], "f-gone": "kept", "group_size": 3,
            "group_members": [{"name": "x", "email": "x@example.com"}]})
        answers = attendees(env)["items"][0]["custom_answers"]
        assert answers == [
            {"field_id": "f-size", "label": "T-shirt size", "value": "XL"},
            {"field_id": "f-diet", "label": "Diet", "value": ["vegan", "nut-free"]},
            {"field_id": "f-gone", "label": "f-gone", "value": "kept"},  # form no longer defines it: key is the label
        ]

    def test_events_without_a_pinned_version_use_the_legacy_default_form(self, env, monkeypatch):
        import app.services.event_attendee_service as attendee_service

        # The service must reach for the real legacy id ...
        assert attendee_service.LEGACY_VERSION_ID == LEGACY_VERSION_ID
        # ... but that id is all digits, and SQLite (NUMERIC affinity for the UUID column type) would coerce it
        # to a float. Postgres is unaffected, so the test swaps in an id with letters.
        legacy_id = str(uuid4())
        monkeypatch.setattr(attendee_service, "LEGACY_VERSION_ID", legacy_id)
        add_form_version(env, [{"id": "s", "fields": [{"id": "legacy-1", "label": "Legacy question", "source": "custom"}]}],
                         version_id=legacy_id)
        add_reg(env, "a@example.com", custom_fields={"legacy-1": True})
        assert attendees(env)["items"][0]["custom_answers"] == [
            {"field_id": "legacy-1", "label": "Legacy question", "value": True}]

    def test_bookkeeping_keys_and_non_dict_answers_are_not_answers(self, env):
        add_reg(env, "leader@example.com", custom_fields={"group_size": 2, "group_members": []})
        add_reg(env, "member@example.com", custom_fields={"group_leader": "leader@example.com"})
        add_reg(env, "legacy-null@example.com")
        env.db.query(EventRegistration).filter_by(participant_email="legacy-null@example.com").update({"custom_fields": None})
        env.db.commit()
        by_email = {i["participant_email"]: i["custom_answers"] for i in attendees(env)["items"]}
        assert by_email["leader@example.com"] == []
        assert by_email["member@example.com"] == []  # group_leader is bookkeeping too
        assert by_email["legacy-null@example.com"] == []


# ===========================================================================
# 13. No N+1
# ===========================================================================


class _Counter:
    def __init__(self, db):
        self.engine = db.get_bind()
        self.selects = 0

    def __enter__(self):
        sa_event.listen(self.engine, "before_cursor_execute", self._count)
        return self

    def __exit__(self, *exc):
        sa_event.remove(self.engine, "before_cursor_execute", self._count)

    def _count(self, conn, cursor, statement, *args):
        if statement.lstrip().upper().startswith("SELECT"):
            self.selects += 1


class TestQueryCount:
    def populate(self, env, count, start=0):
        for i in range(start, start + count):
            email = f"n{i}@example.com"
            add_reg(env, email, ticket_type_id="general", custom_fields={"q": str(i)})
            make_order(env.db, env.event, email, created_at=datetime(2020, 1, 1))

    def measure(self, env, path):
        client = owner_client(env)
        with _Counter(env.db) as counter:
            assert client.get(f"{API}/{env.event.id}{path}").status_code == 200
        return counter.selects

    def test_list_statement_count_does_not_grow_with_rows(self, env):
        self.populate(env, 3)
        few = self.measure(env, "/attendees?page_size=100")
        self.populate(env, 60, start=3)
        many = self.measure(env, "/attendees?page_size=100")
        assert few == many

    def test_payment_filtered_list_statement_count_is_constant(self, env):
        self.populate(env, 3)
        few = self.measure(env, "/attendees?payment_status=paid&q=n")
        self.populate(env, 60, start=3)
        assert self.measure(env, "/attendees?payment_status=paid&q=n&page_size=100") == few

    def test_export_statement_count_is_constant(self, env):
        self.populate(env, 3)
        few = self.measure(env, "/registrations/export")
        self.populate(env, 60, start=3)
        assert self.measure(env, "/registrations/export") == few


# ===========================================================================
# 14. CSV export
# ===========================================================================


def parse_csv(resp):
    return list(csv.reader(io.StringIO(resp.text)))


class TestExport:
    def test_headers_media_type_and_filename(self, env):
        add_reg(env, "a@example.com")
        resp = owner_client(env).get(f"{API}/{env.event.id}/registrations/export")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert resp.headers["content-disposition"] == f"attachment; filename=event_{env.event.id}_registrations.csv"

    def test_original_five_columns_come_first_and_unchanged(self, env):
        reg = add_reg(env, "a@example.com", name="Ann")
        rows = parse_csv(owner_client(env).get(f"{API}/{env.event.id}/registrations/export"))
        assert rows[0][:5] == ["id", "name", "email", "status", "qr_code"]
        assert rows[1][:5] == [str(reg.id), "Ann", "a@example.com", "confirmed", reg.qr_code]

    def test_extra_columns_carry_ticket_payment_and_checkin(self, env):
        add_reg(env, "a@example.com", ticket_type_id="general", status="attended", checked_in_at=datetime(2026, 3, 1, 9, 0))
        order = make_order(env.db, env.event, "a@example.com", quantity="2", amount="1000", created_at=datetime(2020, 1, 1))
        rows = parse_csv(owner_client(env).get(f"{API}/{env.event.id}/registrations/export"))
        row = dict(zip(rows[0], rows[1]))
        assert row["ticket_type"] == "General" and row["quantity"] == "2" and row["payment_status"] == "paid"
        assert row["order_id"] == str(order.id) and row["amount"] == "1000.0" and row["currency"] == "INR"
        assert row["checked_in"] == "yes" and row["checked_in_at"].startswith("2026-03-01T09:00")

    def test_export_honours_the_same_filters_as_the_list(self, env):
        add_reg(env, "vip@example.com", ticket_type_id="vip", status="attended")
        add_reg(env, "gen@example.com", ticket_type_id="general")
        client = owner_client(env)
        url = f"{API}/{env.event.id}/registrations/export"
        assert [r[2] for r in parse_csv(client.get(url, params={"ticket_type_id": "vip"}))[1:]] == ["vip@example.com"]
        assert [r[2] for r in parse_csv(client.get(url, params={"checked_in": "false"}))[1:]] == ["gen@example.com"]
        assert len(parse_csv(client.get(url))) == 3
        assert client.get(url, params={"payment_status": "nonsense"}).status_code == 422

    def test_spreadsheet_formulas_are_neutralised(self, env):
        add_reg(env, "=cmd@example.com", name='=HYPERLINK("http://evil","x")')
        add_reg(env, "plus@example.com", name="+1+1", custom_fields={"q": "@SUM(A1)"})
        add_reg(env, "dash@example.com", name="-2+3")
        rows = {r[2]: r for r in parse_csv(owner_client(env).get(f"{API}/{env.event.id}/registrations/export"))[1:]}
        assert rows["'=cmd@example.com"][1].startswith("'=HYPERLINK")
        assert rows["plus@example.com"][1] == "'+1+1"
        assert rows["dash@example.com"][1] == "'-2+3"
        assert rows["plus@example.com"][-1] == "q: @SUM(A1)"  # the label leads the cell, so it is not a formula

    def test_answers_column_uses_labels(self, env):
        version = add_form_version(env, [{"id": "s", "fields": [{"id": "f1", "label": "Diet", "source": "custom"}]}])
        env.event.form_configuration_version_id = version.id
        env.db.commit()
        add_reg(env, "a@example.com", custom_fields={"f1": ["vegan", "halal"]})
        rows = parse_csv(owner_client(env).get(f"{API}/{env.event.id}/registrations/export"))
        assert rows[1][-1] == "Diet: vegan; halal"

    def test_commas_quotes_and_newlines_stay_in_their_cell(self, env):
        add_reg(env, "a@example.com", name='Smith, "Jr"\nLine2')
        rows = parse_csv(owner_client(env).get(f"{API}/{env.event.id}/registrations/export"))
        assert rows[1][1] == 'Smith, "Jr"\nLine2' and len(rows) == 2

    def test_export_is_scoped_to_its_event(self, env):
        other = make_event(env.db, env.tenant, env.ent)
        add_reg(env, "mine@example.com")
        add_reg(env, "theirs@example.com", event=other)
        rows = parse_csv(owner_client(env).get(f"{API}/{env.event.id}/registrations/export"))
        assert [r[2] for r in rows[1:]] == ["mine@example.com"]

    def test_empty_event_exports_headers_only(self, env):
        rows = parse_csv(owner_client(env).get(f"{API}/{env.event.id}/registrations/export"))
        assert len(rows) == 1 and rows[0][0] == "id"


# ===========================================================================
# 15. Security / tenancy
# ===========================================================================


class TestSecurity:
    ROUTES = ("/attendees", "/registrations/export", "/dashboard")

    @pytest.fixture
    def with_data(self, env):
        add_reg(env, "secret@example.com")
        return env

    @pytest.mark.parametrize("path", ROUTES)
    def test_owning_admin_and_provider_are_allowed(self, with_data, path):
        env = with_data
        for role in ("admin", "provider"):
            resp = client_for(env.db, staff_user(env.tenant, role)).get(f"{API}/{env.event.id}{path}")
            assert resp.status_code == 200, (role, resp.text)

    @pytest.mark.parametrize("path", ROUTES)
    def test_foreign_tenant_staff_are_denied_and_learn_nothing(self, with_data, path):
        env = with_data
        for role in ("admin", "provider"):
            resp = client_for(env.db, staff_user(uuid4(), role)).get(f"{API}/{env.event.id}{path}")
            assert resp.status_code == 403
            assert "secret@example.com" not in resp.text

    @pytest.mark.parametrize("path", ROUTES)
    def test_customers_and_anonymous_callers_are_denied(self, with_data, path):
        env = with_data
        assert client_for(env.db, customer_user("secret@example.com")).get(f"{API}/{env.event.id}{path}").status_code == 403
        assert client_for(env.db, None).get(f"{API}/{env.event.id}{path}").status_code == 401

    @pytest.mark.parametrize("path", ROUTES)
    def test_active_platform_super_admin_is_allowed_across_tenants(self, with_data, path):
        env = with_data
        assert client_for(env.db, super_admin_user()).get(f"{API}/{env.event.id}{path}").status_code == 200

    @pytest.mark.parametrize("path", ROUTES)
    def test_inactive_super_admin_is_denied(self, with_data, path):
        env = with_data
        inactive = {**super_admin_user(), "status": "disabled"}
        assert client_for(env.db, inactive).get(f"{API}/{env.event.id}{path}").status_code == 403

    def test_detail_is_protected_the_same_way(self, with_data):
        env = with_data
        reg = env.db.query(EventRegistration).first()
        url = f"{API}/{env.event.id}/registrations/{reg.id}"
        assert client_for(env.db, staff_user(uuid4(), "admin")).get(url).status_code == 403
        assert client_for(env.db, customer_user("secret@example.com")).get(url).status_code == 403
        assert client_for(env.db, None).get(url).status_code == 401
        assert client_for(env.db, super_admin_user()).get(url).status_code == 200

    @pytest.mark.parametrize("path", ROUTES)
    def test_unknown_event_is_404_for_staff(self, env, path):
        assert owner_client(env).get(f"{API}/{uuid4()}{path}").status_code == 404

    def test_a_tenant_in_the_query_string_cannot_widen_access(self, with_data):
        env = with_data
        resp = client_for(env.db, staff_user(uuid4(), "admin")).get(
            f"{API}/{env.event.id}/attendees", params={"tenant_id": str(env.tenant)})
        assert resp.status_code == 403

    def test_a_deleted_event_is_not_manageable(self, env):
        env.event.is_deleted = True
        env.db.commit()
        assert owner_client(env).get(f"{API}/{env.event.id}/attendees").status_code == 404


# ===========================================================================
# 16. Compatibility
# ===========================================================================


class TestLegacyListUntouched:
    def test_registrations_is_still_a_bare_array_of_registration_rows(self, env):
        reg = add_reg(env, "a@example.com", ticket_type_id="general")
        body = owner_client(env).get(f"{API}/{env.event.id}/registrations").json()
        assert isinstance(body, list) and len(body) == 1
        assert body[0]["id"] == str(reg.id) and body[0]["status"] == "confirmed"
        assert body[0]["participant_email"] == "a@example.com" and body[0]["qr_code"] == reg.qr_code
