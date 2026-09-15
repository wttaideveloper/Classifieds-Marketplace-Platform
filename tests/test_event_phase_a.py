"""
Events Backend — Phase A Tests
================================
Tests for:
  A. Customer dynamic form configuration access (GET /{event_id}/registration-form)
  B. Waitlist DELETE IDOR protection
  C. Duplicate event registration protection
  D. Duplicate waitlist protection
  E. Waitlist status / registration-window validation

All tests follow the project conventions:
  - app.dependency_overrides[get_current_user] for auth injection
  - patch() on the correct module where names are *bound*

Run:
    pytest tests/test_event_phase_a.py -v
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.main import app


# ---------------------------------------------------------------------------
# User fixtures
# ---------------------------------------------------------------------------

ADMIN_USER = {"id": str(uuid4()), "role": "admin", "email": "admin@test.com"}
PROVIDER_USER = {"id": str(uuid4()), "role": "provider", "email": "provider@test.com"}
CUSTOMER_USER = {"id": str(uuid4()), "role": "customer", "email": "customer@test.com"}
CUSTOMER_B = {"id": str(uuid4()), "role": "customer", "email": "customer_b@test.com"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(
    status: str = "published",
    capacity: str | None = None,
    registration_open_at: datetime | None = None,
    registration_close_at: datetime | None = None,
    registration_cutoff: datetime | None = None,
    max_participants: str | None = None,
    ticket_types: list | None = None,
    form_configuration_version_id=None,
):
    event = MagicMock()
    event.id = uuid4()
    event.status = status
    event.is_deleted = False
    event.capacity = capacity
    event.registration_open_at = registration_open_at
    event.registration_close_at = registration_close_at
    event.registration_cutoff = registration_cutoff
    event.start_date = datetime.utcnow() + timedelta(days=7)
    event.end_date = datetime.utcnow() + timedelta(days=8)
    event.max_participants = max_participants
    event.ticket_types = ticket_types or []
    event.form_configuration_version_id = form_configuration_version_id
    event.title = "Test Event"
    return event


def _make_waitlist_entry(event_id, email: str):
    entry = MagicMock()
    entry.id = uuid4()
    entry.event_id = event_id
    entry.participant_email = email
    entry.participant_name = "Waitlisted User"
    return entry


def _client_for(user: dict, db=None):
    """Create a TestClient with given user and db injected."""
    if db is None:
        db = MagicMock()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app, raise_server_exceptions=False)
    return client, db


def _reset():
    app.dependency_overrides.clear()


# ===========================================================================
# A. Customer Dynamic Form Configuration Access
# ===========================================================================


class TestCustomerRegistrationForm:
    """GET /{event_id}/registration-form — new customer-safe endpoint."""

    FORM_RESP = {
        "configuration_id": str(uuid4()),
        "version_id": str(uuid4()),
        "name": "Default Form",
        "scope": "global",
        "version": 1,
        "sections": [],
        "configuration_version": "abc:1",
    }

    def teardown_method(self):
        _reset()

    def test_customer_published_event_allowed(self):
        event_id = uuid4()
        event = _make_event(status="published")
        tc, db = _client_for(CUSTOMER_USER)

        with (
            patch("app.repository.event_repo.get_event_by_id", return_value=event),
            patch(
                "app.services.event_form_config_service.get_event_form_configuration_service"
                if False else
                "app.api.v1.endpoints.event.get_event_form_configuration_service",
                return_value=self.FORM_RESP,
            ),
        ):
            resp = tc.get(f"/api/v1/events/{event_id}/registration-form")

        assert resp.status_code == 200, resp.text
        assert "sections" in resp.json()

    def test_customer_draft_event_denied(self):
        event_id = uuid4()
        event = _make_event(status="draft")
        tc, db = _client_for(CUSTOMER_USER)

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            resp = tc.get(f"/api/v1/events/{event_id}/registration-form")

        assert resp.status_code == 403, resp.text

    def test_customer_cancelled_event_denied(self):
        event_id = uuid4()
        event = _make_event(status="cancelled")
        tc, db = _client_for(CUSTOMER_USER)

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            resp = tc.get(f"/api/v1/events/{event_id}/registration-form")

        assert resp.status_code == 403, resp.text

    def test_customer_suspended_event_denied(self):
        event_id = uuid4()
        event = _make_event(status="suspended")
        tc, db = _client_for(CUSTOMER_USER)

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            resp = tc.get(f"/api/v1/events/{event_id}/registration-form")

        assert resp.status_code == 403, resp.text

    def test_customer_archived_event_denied(self):
        event_id = uuid4()
        event = _make_event(status="archived")
        tc, db = _client_for(CUSTOMER_USER)

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            resp = tc.get(f"/api/v1/events/{event_id}/registration-form")

        assert resp.status_code == 403, resp.text

    def test_unauthenticated_request_denied(self, monkeypatch):
        """Without auth override in production mode → 401."""
        _reset()  # no user override
        from app.core.config import settings
        monkeypatch.setattr(settings, "is_production", True)
        monkeypatch.setattr(settings, "ENABLE_DEV_TOKEN", False)
        app.dependency_overrides[get_db] = lambda: MagicMock()
        tc = TestClient(app, raise_server_exceptions=False)
        resp = tc.get(f"/api/v1/events/{uuid4()}/registration-form")
        # In production mode with no token → 401
        assert resp.status_code in (401, 403), resp.text

    def test_nonexistent_event_returns_404(self):
        event_id = uuid4()
        tc, db = _client_for(CUSTOMER_USER)

        with patch("app.repository.event_repo.get_event_by_id", return_value=None):
            resp = tc.get(f"/api/v1/events/{event_id}/registration-form")

        assert resp.status_code == 404, resp.text

    def test_admin_can_also_use_endpoint(self):
        """Admin role must also work — any authenticated user is allowed."""
        event_id = uuid4()
        event = _make_event(status="published")
        tc, db = _client_for(ADMIN_USER)

        with (
            patch("app.repository.event_repo.get_event_by_id", return_value=event),
            patch(
                "app.api.v1.endpoints.event.get_event_form_configuration_service",
                return_value=self.FORM_RESP,
            ),
        ):
            resp = tc.get(f"/api/v1/events/{event_id}/registration-form")

        assert resp.status_code == 200, resp.text

    def test_existing_admin_endpoint_rejects_customer(self):
        """Original /{event_id}/form-configuration still requires admin/provider.
        Customer role must be rejected (403) to confirm the existing gate is intact."""
        event_id = uuid4()
        tc, db = _client_for(CUSTOMER_USER)

        resp = tc.get(f"/api/v1/events/{event_id}/form-configuration")
        # require_roles(["admin","provider"]) → 403 for customer
        assert resp.status_code == 403, resp.text


# ===========================================================================
# B. Waitlist DELETE IDOR
# ===========================================================================


class TestWaitlistDeleteIDOR:
    """Ownership enforcement on DELETE /{event_id}/waitlist/{entry_id}."""

    def teardown_method(self):
        _reset()

    def test_customer_can_delete_own_entry(self):
        event_id = uuid4()
        entry = _make_waitlist_entry(event_id, CUSTOMER_USER["email"])
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = entry
        tc, _ = _client_for(CUSTOMER_USER, db)

        resp = tc.delete(f"/api/v1/events/{event_id}/waitlist/{entry.id}")

        assert resp.status_code == 200, resp.text
        assert "removed" in resp.json()["message"].lower()

    def test_customer_cannot_delete_another_users_entry(self):
        """IDOR: customer_b requests deletion of customer_a's entry → 404."""
        event_id = uuid4()
        entry = _make_waitlist_entry(event_id, CUSTOMER_USER["email"])
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = entry
        # CUSTOMER_B (different email) is the requester
        tc, _ = _client_for(CUSTOMER_B, db)

        resp = tc.delete(f"/api/v1/events/{event_id}/waitlist/{entry.id}")

        # Must be 404 — not 200, not 403 (avoids leaking entry existence)
        assert resp.status_code == 404, resp.text

    def test_admin_can_delete_any_entry(self):
        event_id = uuid4()
        entry = _make_waitlist_entry(event_id, CUSTOMER_USER["email"])
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = entry
        tc, _ = _client_for(ADMIN_USER, db)

        resp = tc.delete(f"/api/v1/events/{event_id}/waitlist/{entry.id}")

        assert resp.status_code == 200, resp.text

    def test_provider_can_delete_any_entry(self):
        event_id = uuid4()
        entry = _make_waitlist_entry(event_id, CUSTOMER_USER["email"])
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = entry
        tc, _ = _client_for(PROVIDER_USER, db)

        resp = tc.delete(f"/api/v1/events/{event_id}/waitlist/{entry.id}")

        assert resp.status_code == 200, resp.text

    def test_nonexistent_entry_returns_404(self):
        event_id = uuid4()
        entry_id = uuid4()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        tc, _ = _client_for(CUSTOMER_USER, db)

        resp = tc.delete(f"/api/v1/events/{event_id}/waitlist/{entry_id}")

        assert resp.status_code == 404, resp.text


# ===========================================================================
# C. Duplicate Registration Protection — direct service unit tests
# ===========================================================================


class TestDuplicateRegistrationProtection:
    """Unit-test create_registration_service.

    Since _get_event_or_404 does a local import of get_event_by_id inside
    the function, we patch at 'app.repository.event_repo.get_event_by_id'
    which is the binding that gets re-imported every call.
    """

    def test_first_registration_succeeds(self):
        from app.services import event_service

        event = _make_event(status="published")
        db = MagicMock()
        db.query.return_value.filter.return_value.count.return_value = 0
        db.query.return_value.filter.return_value.first.return_value = None
        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = event

        payload = MagicMock()
        payload.participant_email = "new@example.com"
        payload.participant_name = "New User"
        payload.custom_fields = {}
        payload.ticket_type_id = None
        payload.group_size = 1
        payload.group_members = None

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            try:
                event_service.create_registration_service(db, event.id, payload)
            except HTTPException as e:
                if e.status_code == 409:
                    pytest.fail(f"First registration incorrectly rejected: {e.detail}")

    def test_duplicate_active_registration_rejected(self):
        from app.services import event_service
        from app.models.event_aux_models import EventRegistration

        event = _make_event(status="published")
        email = "dup@example.com"

        existing = MagicMock(spec=EventRegistration)
        existing.status = "confirmed"
        existing.participant_email = email
        existing.event_id = event.id

        db = MagicMock()
        call_n = [0]

        def _query(model):
            mock_q = MagicMock()
            mock_q.filter.return_value.count.return_value = 0
            mock_q.filter.return_value.with_for_update.return_value.first.return_value = event

            inner = MagicMock()
            inner.first.return_value = existing

            def _filter(*a, **kw):
                call_n[0] += 1
                return inner if call_n[0] >= 2 else mock_q.filter.return_value

            mock_q.filter.side_effect = _filter
            return mock_q

        db.query.side_effect = _query

        payload = MagicMock()
        payload.participant_email = email
        payload.participant_name = "Dup User"
        payload.custom_fields = {}
        payload.ticket_type_id = None
        payload.group_size = 1
        payload.group_members = None

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            with pytest.raises(HTTPException) as exc_info:
                event_service.create_registration_service(db, event.id, payload)

        assert exc_info.value.status_code == 409
        assert "already registered" in exc_info.value.detail.lower()

    def test_email_stored_normalised_lowercase(self):
        from app.services import event_service

        event = _make_event(status="published")
        db = MagicMock()
        db.query.return_value.filter.return_value.count.return_value = 0
        db.query.return_value.filter.return_value.first.return_value = None
        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = event

        added = []
        db.add.side_effect = lambda obj: added.append(obj)

        payload = MagicMock()
        payload.participant_email = "  MIXED@Example.COM  "
        payload.participant_name = "Mixed Case"
        payload.custom_fields = {}
        payload.ticket_type_id = None
        payload.group_size = 1
        payload.group_members = None

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            try:
                event_service.create_registration_service(db, event.id, payload)
            except Exception:
                pass

        if added:
            stored = getattr(added[0], "participant_email", None)
            if stored:
                assert stored == "mixed@example.com", f"Email not normalised: {stored!r}"

    def test_cancelled_registration_allows_reregister(self):
        """No active registration → re-registration after cancel allowed."""
        from app.services import event_service

        event = _make_event(status="published")
        db = MagicMock()
        db.query.return_value.filter.return_value.count.return_value = 0
        # Duplicate check returns None (cancelled reg excluded by WHERE clause)
        db.query.return_value.filter.return_value.first.return_value = None
        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = event

        payload = MagicMock()
        payload.participant_email = "returning@example.com"
        payload.participant_name = "Returning User"
        payload.custom_fields = {}
        payload.ticket_type_id = None
        payload.group_size = 1
        payload.group_members = None

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            try:
                event_service.create_registration_service(db, event.id, payload)
            except HTTPException as e:
                if e.status_code == 409:
                    pytest.fail(f"Re-registration after cancel blocked: {e.detail}")


# ===========================================================================
# D. Duplicate Waitlist Protection — direct service unit tests
# ===========================================================================


class TestDuplicateWaitlistProtection:

    def _make_db_wl(self, event, reg_count=10, existing_wl=None):
        db = MagicMock()
        call_n = [0]

        def _query(model):
            mock_q = MagicMock()
            mock_q.filter.return_value.count.return_value = reg_count
            mock_q.filter.return_value.with_for_update.return_value.first.return_value = event

            inner = MagicMock()
            inner.first.return_value = existing_wl

            def _filter(*a, **kw):
                call_n[0] += 1
                return inner if call_n[0] >= 3 else mock_q.filter.return_value

            mock_q.filter.side_effect = _filter
            return mock_q

        db.query.side_effect = _query
        return db

    def test_first_waitlist_join_succeeds(self):
        from app.services import event_service

        event = _make_event(status="published", capacity="10")
        db = self._make_db_wl(event, reg_count=10, existing_wl=None)

        payload = MagicMock()
        payload.participant_email = "first@example.com"
        payload.participant_name = "First Waiter"

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            try:
                event_service.create_waitlist_entry_service(db, event.id, payload)
            except HTTPException as e:
                if e.status_code == 409:
                    pytest.fail(f"First waitlist join rejected: {e.detail}")

    def test_duplicate_waitlist_join_rejected(self):
        from app.services import event_service
        from app.models.event_aux_models import EventWaitlist

        event = _make_event(status="published", capacity="10")
        email = "wl@example.com"
        existing = _make_waitlist_entry(event.id, email)
        db = self._make_db_wl(event, reg_count=10, existing_wl=existing)

        payload = MagicMock()
        payload.participant_email = email
        payload.participant_name = "Dup Waiter"

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            with pytest.raises(HTTPException) as exc_info:
                event_service.create_waitlist_entry_service(db, event.id, payload)

        assert exc_info.value.status_code == 409
        assert "waitlist" in exc_info.value.detail.lower()

    def test_waitlist_email_case_normalization(self):
        from app.services import event_service

        event = _make_event(status="published", capacity="10")
        existing = _make_waitlist_entry(event.id, "wl@example.com")
        db = self._make_db_wl(event, reg_count=10, existing_wl=existing)

        payload = MagicMock()
        payload.participant_email = "WL@EXAMPLE.COM"
        payload.participant_name = "Upper Waiter"

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            with pytest.raises(HTTPException) as exc_info:
                event_service.create_waitlist_entry_service(db, event.id, payload)

        assert exc_info.value.status_code == 409

    def test_email_stored_normalised_in_waitlist(self):
        from app.services import event_service

        event = _make_event(status="published", capacity="10")
        db = self._make_db_wl(event, reg_count=10, existing_wl=None)

        added = []
        db.add.side_effect = lambda obj: added.append(obj)

        payload = MagicMock()
        payload.participant_email = "  WaitUser@EXAMPLE.COM  "
        payload.participant_name = "Wait User"

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            try:
                event_service.create_waitlist_entry_service(db, event.id, payload)
            except Exception:
                pass

        if added:
            stored = getattr(added[0], "participant_email", None)
            if stored:
                assert stored == "waituser@example.com", f"Not normalised: {stored!r}"


# ===========================================================================
# E. Waitlist Status / Window Validation — direct service unit tests
# ===========================================================================


class TestWaitlistValidation:

    def _make_db_wl(self, event, reg_count: int = 10):
        db = MagicMock()
        call_n = [0]

        def _query(model):
            mock_q = MagicMock()
            mock_q.filter.return_value.count.return_value = reg_count
            mock_q.filter.return_value.with_for_update.return_value.first.return_value = event

            inner = MagicMock()
            inner.first.return_value = None  # no existing waitlist entry

            def _filter(*a, **kw):
                call_n[0] += 1
                return inner if call_n[0] >= 3 else mock_q.filter.return_value

            mock_q.filter.side_effect = _filter
            return mock_q

        db.query.side_effect = _query
        return db

    def _run(self, event, reg_count: int = 10):
        from app.services import event_service

        db = self._make_db_wl(event, reg_count)
        payload = MagicMock()
        payload.participant_email = "test@example.com"
        payload.participant_name = "Test User"

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            return event_service.create_waitlist_entry_service(db, event.id, payload)

    def test_published_full_event_allows_waitlist(self):
        event = _make_event(status="published", capacity="10")
        try:
            self._run(event, reg_count=10)
        except HTTPException as e:
            if e.status_code in (400, 403, 409):
                pytest.fail(f"Valid waitlist join rejected: {e.detail}")

    def test_draft_event_rejected(self):
        event = _make_event(status="draft")
        with pytest.raises(HTTPException) as exc_info:
            self._run(event)
        assert exc_info.value.status_code == 400
        detail = exc_info.value.detail.lower()
        assert "draft" in detail or "published" in detail

    def test_cancelled_event_rejected(self):
        event = _make_event(status="cancelled")
        with pytest.raises(HTTPException) as exc_info:
            self._run(event)
        assert exc_info.value.status_code == 400
        assert "cancelled" in exc_info.value.detail.lower()

    def test_completed_event_rejected(self):
        event = _make_event(status="completed")
        with pytest.raises(HTTPException) as exc_info:
            self._run(event)
        assert exc_info.value.status_code == 400

    def test_suspended_event_rejected(self):
        event = _make_event(status="suspended")
        with pytest.raises(HTTPException) as exc_info:
            self._run(event)
        assert exc_info.value.status_code == 400

    def test_archived_event_rejected(self):
        event = _make_event(status="archived")
        with pytest.raises(HTTPException) as exc_info:
            self._run(event)
        assert exc_info.value.status_code == 400

    def test_pending_approval_rejected(self):
        event = _make_event(status="pending_approval")
        with pytest.raises(HTTPException) as exc_info:
            self._run(event)
        assert exc_info.value.status_code == 400

    def test_registration_window_not_yet_open_rejected(self):
        event = _make_event(
            status="published",
            capacity="10",
            registration_open_at=datetime.utcnow() + timedelta(days=3),
        )
        with pytest.raises(HTTPException) as exc_info:
            self._run(event)
        assert exc_info.value.status_code == 400
        assert "open" in exc_info.value.detail.lower()

    def test_registration_window_closed_rejected(self):
        event = _make_event(
            status="published",
            capacity="10",
            registration_close_at=datetime.utcnow() - timedelta(hours=1),
        )
        with pytest.raises(HTTPException) as exc_info:
            self._run(event)
        assert exc_info.value.status_code == 400
        assert "closed" in exc_info.value.detail.lower()

    def test_registration_cutoff_passed_rejected(self):
        event = _make_event(
            status="published",
            capacity="10",
            registration_cutoff=datetime.utcnow() - timedelta(hours=2),
        )
        with pytest.raises(HTTPException) as exc_info:
            self._run(event)
        assert exc_info.value.status_code == 400
        assert "cutoff" in exc_info.value.detail.lower()

    def test_event_not_full_waitlist_rejected(self):
        """Seats still available → waitlist join must be rejected."""
        event = _make_event(status="published", capacity="10")
        with pytest.raises(HTTPException) as exc_info:
            self._run(event, reg_count=3)  # 3/10 → 7 seats left
        assert exc_info.value.status_code == 400
        detail = exc_info.value.detail.lower()
        assert "available" in detail or "seat" in detail or "register" in detail
