"""
Events Backend — Paid Checkout Tests
======================================
Regression tests for POST /{event_id}/checkout (create_event_checkout_service).

Root cause covered by this suite: the function referenced the `Event` model
class (for the capacity-lock query) without ever importing it — neither at
module level nor locally within the function, unlike every other function in
event_service.py that uses `Event` (all of which do a local
`from app.models.event_model import Event`). This raised a NameError on
every single checkout attempt, caught by the function's generic
`except Exception` handler and surfaced as HTTP 500
"An error occurred during checkout processing." — for every user, on every
event, 100% reproducibly, regardless of any registration/capacity/ticket
data. Fixed by adding the same local import used everywhere else in this
file (see create_event_checkout_service's local imports).

Covers:
  A. Successful demo paid checkout creates EventOrder + EventRegistration
  B. Duplicate active registration returns 409
  C. Invalid/foreign ticket_type_id returns 404
  D. Closed event / registration window rejected (400)
  E. Insufficient ticket capacity rejected (400)
  F. Order + registration are atomic (both added, single commit)
  G. A database failure during commit rolls back both (500 or 409, never a
     half-created order/registration)

Follows the project conventions established in test_event_phase_a.py:
  - MagicMock() session, branching db.query(...) by model identity
  - patch("app.repository.event_repo.get_event_by_id", ...) for event lookup
  - direct unit calls into event_service.create_event_checkout_service

Run:
    pytest tests/test_event_checkout.py -v
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.models.event_aux_models import EventOrder, EventRegistration
from app.models.event_model import Event

EARLY_BIRD_TICKET_ID = "ae2e1ff8-080e-4569-a465-b7e4ea490b99"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    status: str = "published",
    ticket_types: list | None = None,
    registration_open_at=None,
    registration_close_at=None,
    registration_cutoff=None,
    price: str | None = "0",
    currency: str = "INR",
):
    event = MagicMock(spec=Event)
    event.id = uuid4()
    event.status = status
    event.is_deleted = False
    event.registration_open_at = registration_open_at
    event.registration_close_at = registration_close_at
    event.registration_cutoff = registration_cutoff
    event.price = price
    event.currency = currency
    event.ticket_types = ticket_types or []
    event.title = "Paid Test Event"
    return event


def _make_ticket(
    ticket_id: str = EARLY_BIRD_TICKET_ID,
    name: str = "Early Bird",
    price: str = "999",
    currency: str = "INR",
    capacity=None,
    early_bird_price: str | None = "799",
    early_bird_until: str | None = None,
):
    if early_bird_until is None:
        early_bird_until = (datetime.utcnow() + timedelta(days=30)).isoformat()
    return {
        "id": ticket_id,
        "name": name,
        "price": price,
        "currency": currency,
        "capacity": capacity,
        "early_bird_price": early_bird_price,
        "early_bird_until": early_bird_until,
    }


def _make_payload(
    participant_email: str = "new-buyer@example.com",
    participant_name: str = "New Buyer",
    ticket_type_id: str | None = EARLY_BIRD_TICKET_ID,
    quantity: int = 1,
    payment_provider: str | None = None,
):
    payload = MagicMock()
    payload.participant_email = participant_email
    payload.participant_name = participant_name
    payload.ticket_type_id = ticket_type_id
    payload.quantity = quantity
    payload.payment_provider = payment_provider
    return payload


def _make_db(
    event,
    existing_reg=None,
    order_count: int = 0,
    reg_count: int = 0,
    commit_side_effect=None,
):
    """Branches db.query(Model) by identity so the checkout function's several
    different queries (Event lock, EventRegistration duplicate-check,
    EventOrder/EventRegistration capacity counts) each get the right canned
    result — mirrors the pattern already used in test_event_phase_a.py."""
    db = MagicMock()

    def _query(model):
        q = MagicMock()
        if model is Event:
            q.filter.return_value.with_for_update.return_value.first.return_value = event
        elif model is EventRegistration:
            q.filter.return_value.first.return_value = existing_reg
            q.filter.return_value.count.return_value = reg_count
        elif model is EventOrder:
            q.filter.return_value.count.return_value = order_count
        return q

    db.query.side_effect = _query

    added = []
    db.add.side_effect = lambda obj: added.append(obj)
    db.added = added  # test-only convenience handle

    if commit_side_effect is not None:
        db.commit.side_effect = commit_side_effect

    return db


# ===========================================================================
# A. Successful demo paid checkout
# ===========================================================================


class TestSuccessfulDemoCheckout:
    def test_checkout_creates_order_and_registration(self):
        from app.services import event_service

        ticket = _make_ticket()
        event = _make_event(status="published", ticket_types=[ticket])
        db = _make_db(event, existing_reg=None, order_count=0, reg_count=0)
        payload = _make_payload()

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            order = event_service.create_event_checkout_service(db, event.id, payload)

        assert isinstance(order, EventOrder)
        assert order.participant_email == "new-buyer@example.com"
        assert order.ticket_type_id == EARLY_BIRD_TICKET_ID
        assert order.payment_status == "confirmed"
        assert order.status == "confirmed"
        # The post-commit notification pipeline (notify_payment_success →
        # notification_repo.create_notification) does its own separate,
        # unrelated commit for the audit-log row — so at least one commit
        # is expected here, not exactly one; what matters for atomicity is
        # asserted in TestCheckoutAtomicity below (order+registration land
        # in the same commit, and no rollback happens on the success path).
        assert db.commit.called
        db.rollback.assert_not_called()

    def test_checkout_uses_backend_recomputed_price(self):
        """The backend recomputes the authoritative price server-side — it
        must match the early-bird price, not merely echo whatever the
        client happened to display."""
        from app.services import event_service

        ticket = _make_ticket(price="999", early_bird_price="799")
        event = _make_event(status="published", ticket_types=[ticket])
        db = _make_db(event)
        payload = _make_payload()

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            order = event_service.create_event_checkout_service(db, event.id, payload)

        assert order.amount == "799.0"
        assert order.currency == "INR"

    def test_checkout_defaults_payment_provider_to_marketplace(self):
        from app.services import event_service

        ticket = _make_ticket()
        event = _make_event(status="published", ticket_types=[ticket])
        db = _make_db(event)
        payload = _make_payload(payment_provider=None)

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            order = event_service.create_event_checkout_service(db, event.id, payload)

        assert order.payment_provider == "marketplace"


# ===========================================================================
# B. Duplicate registration protection
# ===========================================================================


class TestDuplicateCheckoutRejected:
    def test_duplicate_registration_returns_409(self):
        from app.services import event_service

        ticket = _make_ticket()
        event = _make_event(status="published", ticket_types=[ticket])
        existing = MagicMock(spec=EventRegistration)
        existing.status = "confirmed"
        db = _make_db(event, existing_reg=existing)
        payload = _make_payload(participant_email="repeat@example.com")

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            with pytest.raises(HTTPException) as exc_info:
                event_service.create_event_checkout_service(db, event.id, payload)

        assert exc_info.value.status_code == 409
        assert "already registered" in exc_info.value.detail.lower()
        db.rollback.assert_called_once()
        db.commit.assert_not_called()


# ===========================================================================
# C. Invalid ticket
# ===========================================================================


class TestInvalidTicketRejected:
    def test_unknown_ticket_type_returns_404(self):
        from app.services import event_service

        event = _make_event(
            status="published",
            ticket_types=[_make_ticket(ticket_id=str(uuid4()))],
        )
        db = _make_db(event)
        payload = _make_payload(ticket_type_id=str(uuid4()))  # not on this event

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            with pytest.raises(HTTPException) as exc_info:
                event_service.create_event_checkout_service(db, event.id, payload)

        assert exc_info.value.status_code == 404
        assert "ticket type not found" in exc_info.value.detail.lower()


# ===========================================================================
# D. Event state / registration window
# ===========================================================================


class TestEventStateAndWindowRejected:
    @pytest.mark.parametrize(
        "status",
        ["cancelled", "completed", "archived", "suspended", "draft", "pending_approval"],
    )
    def test_non_published_event_rejected(self, status):
        from app.services import event_service

        event = _make_event(status=status, ticket_types=[_make_ticket()])
        db = _make_db(event)
        payload = _make_payload()

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            with pytest.raises(HTTPException) as exc_info:
                event_service.create_event_checkout_service(db, event.id, payload)

        assert exc_info.value.status_code == 400

    def test_registration_not_yet_open_rejected(self):
        from app.services import event_service

        event = _make_event(
            status="published",
            ticket_types=[_make_ticket()],
            registration_open_at=datetime.utcnow() + timedelta(days=1),
        )
        db = _make_db(event)
        payload = _make_payload()

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            with pytest.raises(HTTPException) as exc_info:
                event_service.create_event_checkout_service(db, event.id, payload)

        assert exc_info.value.status_code == 400
        assert "not yet open" in exc_info.value.detail.lower()

    def test_registration_closed_rejected(self):
        from app.services import event_service

        event = _make_event(
            status="published",
            ticket_types=[_make_ticket()],
            registration_close_at=datetime.utcnow() - timedelta(hours=1),
        )
        db = _make_db(event)
        payload = _make_payload()

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            with pytest.raises(HTTPException) as exc_info:
                event_service.create_event_checkout_service(db, event.id, payload)

        assert exc_info.value.status_code == 400
        assert "closed" in exc_info.value.detail.lower()

    def test_registration_cutoff_passed_rejected(self):
        from app.services import event_service

        event = _make_event(
            status="published",
            ticket_types=[_make_ticket()],
            registration_cutoff=datetime.utcnow() - timedelta(hours=1),
        )
        db = _make_db(event)
        payload = _make_payload()

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            with pytest.raises(HTTPException) as exc_info:
                event_service.create_event_checkout_service(db, event.id, payload)

        assert exc_info.value.status_code == 400
        assert "cutoff" in exc_info.value.detail.lower()


# ===========================================================================
# E. Capacity
# ===========================================================================


class TestCapacityRejected:
    def test_ticket_at_capacity_rejected(self):
        from app.services import event_service

        ticket = _make_ticket(capacity=5)
        event = _make_event(status="published", ticket_types=[ticket])
        # 3 confirmed orders + 2 confirmed/attended registrations already == capacity (5)
        db = _make_db(event, order_count=3, reg_count=2)
        payload = _make_payload(quantity=1)

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            with pytest.raises(HTTPException) as exc_info:
                event_service.create_event_checkout_service(db, event.id, payload)

        assert exc_info.value.status_code == 400
        assert "capacity" in exc_info.value.detail.lower()
        db.rollback.assert_called_once()

    def test_ticket_under_capacity_allowed(self):
        from app.services import event_service

        ticket = _make_ticket(capacity=5)
        event = _make_event(status="published", ticket_types=[ticket])
        db = _make_db(event, order_count=1, reg_count=1)  # 2/5 used
        payload = _make_payload(quantity=1)

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            order = event_service.create_event_checkout_service(db, event.id, payload)

        assert isinstance(order, EventOrder)


# ===========================================================================
# F/G. Atomicity and rollback
# ===========================================================================


class TestCheckoutAtomicity:
    def test_order_and_registration_both_added_before_single_commit(self):
        from app.services import event_service

        ticket = _make_ticket()
        event = _make_event(status="published", ticket_types=[ticket])
        db = _make_db(event)
        payload = _make_payload(participant_email="atomic@example.com")

        # Track the interleaving of add()/commit() calls directly, rather
        # than assuming exactly one commit for the whole request — the
        # post-commit notification pipeline does its own separate commit
        # for its audit-log row (see test_checkout_creates_order_and_registration).
        call_order = []
        db.add.side_effect = lambda obj: call_order.append(("add", obj))
        db.commit.side_effect = lambda: call_order.append(("commit", None))

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            event_service.create_event_checkout_service(db, event.id, payload)

        added = [obj for kind, obj in call_order if kind == "add"]
        added_types = [type(obj) for obj in added]
        assert EventOrder in added_types
        assert EventRegistration in added_types

        reg = next(obj for obj in added if isinstance(obj, EventRegistration))
        assert reg.participant_email == "atomic@example.com"
        assert reg.ticket_type_id == EARLY_BIRD_TICKET_ID
        assert reg.qr_code  # a real QR code was generated

        # The order's and registration's own add() calls must both precede
        # the checkout's own (first) commit — i.e. they are written in the
        # SAME transaction, not two separate ones. (The notification
        # pipeline may issue its own additional db.add() for an audit-log
        # row after that commit — irrelevant to order/registration atomicity.)
        first_commit_index = next(i for i, (kind, _) in enumerate(call_order) if kind == "commit")
        order_reg_add_indices = [
            i
            for i, (kind, obj) in enumerate(call_order)
            if kind == "add" and isinstance(obj, (EventOrder, EventRegistration))
        ]
        assert len(order_reg_add_indices) == 2
        assert all(i < first_commit_index for i in order_reg_add_indices)

    def test_database_failure_during_commit_rolls_back_both(self):
        """If commit() fails for an unexpected reason, nothing is left
        half-created and the client sees a 500, not a fabricated success."""
        from app.services import event_service

        ticket = _make_ticket()
        event = _make_event(status="published", ticket_types=[ticket])
        db = _make_db(event, commit_side_effect=RuntimeError("simulated DB outage"))
        payload = _make_payload()

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            with pytest.raises(HTTPException) as exc_info:
                event_service.create_event_checkout_service(db, event.id, payload)

        assert exc_info.value.status_code == 500
        db.rollback.assert_called_once()

    def test_integrity_error_during_commit_returns_409_not_500(self):
        """A race-condition duplicate caught only at the DB level (two
        concurrent checkouts for the same participant) still maps to 409,
        not a generic 500."""
        from app.services import event_service

        ticket = _make_ticket()
        event = _make_event(status="published", ticket_types=[ticket])
        db = _make_db(
            event,
            commit_side_effect=IntegrityError("stmt", {}, Exception("unique violation")),
        )
        payload = _make_payload()

        with patch("app.repository.event_repo.get_event_by_id", return_value=event):
            with pytest.raises(HTTPException) as exc_info:
                event_service.create_event_checkout_service(db, event.id, payload)

        assert exc_info.value.status_code == 409
        db.rollback.assert_called_once()
