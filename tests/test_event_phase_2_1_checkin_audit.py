"""
Event Management Phase 2.1 — check-in hardening and audit actor / transaction behaviour.

Real (in-memory SQLite) database via tests/event_sql_support.py.

Check-in: cancelled/refunded registrations are invalid, check-out needs a prior check-in, uncheck-in
clears the check-out, batch check-in records the operator, session_id must belong to the Event,
cross-tenant staff are blocked. Existing endpoint shapes are preserved.

Audit: EventAudit rows carry the acting user, are staged in the same transaction as the change they
describe (no mid-request commit), and can no longer fail on datetimes/UUIDs.

Run:
    pytest tests/test_event_phase_2_1_checkin_audit.py -v
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from event_sql_support import (
    API,
    Event,
    EventAudit,
    EventOrder,
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
)


@pytest.fixture
def env(monkeypatch):
    silence_side_effects(monkeypatch)
    db = make_session()
    tenant = uuid4()
    ent = make_enterprise(db, tenant)
    session_date = (datetime.utcnow() + timedelta(days=10)).date().isoformat()
    event = make_event(db, tenant, ent, sessions=[{"id": "sess-1", "session_date": session_date, "title": "Keynote"}])
    owner = staff_user(tenant, "admin")
    yield SimpleNamespace(db=db, tenant=tenant, ent=ent, event=event, owner=owner, staff=client_for(db, owner))
    reset_overrides()
    db.close()


def audits(db, action=None):
    db.expire_all()
    q = db.query(EventAudit)
    if action:
        q = q.filter(EventAudit.action == action)
    return q.order_by(EventAudit.created_at).all()


def reg_state(db, reg):
    db.expire_all()
    fresh = db.get(EventRegistration, reg.id)
    return fresh.status, fresh.checked_in_at, fresh.checked_out_at, fresh.checked_in_by, fresh.session_id


def count_commits(db):
    calls = []
    original = db.commit
    db.commit = lambda: (calls.append(1), original())[1]
    return calls


# ===========================================================================
# Check-in hardening
# ===========================================================================


class TestCancelledAndRefundedRegistrations:
    def test_cancelled_registration_cannot_check_in_and_validates_as_invalid(self, env):
        reg = make_registration(env.db, env.event, "gone@example.com", status="cancelled")
        assert env.staff.post(f"{API}/{env.event.id}/check-in", json={"registration_id": str(reg.id)}).status_code == 400
        body = env.staff.post(f"{API}/{env.event.id}/validate-qr", json={"qr_code": reg.qr_code}).json()
        assert body["valid"] is False and "cancelled" in body["message"].lower()
        assert body["registration_id"] == str(reg.id) and body["status"] == "cancelled"  # staff still see who it was
        assert reg_state(env.db, reg)[0] == "cancelled"

    def test_active_registration_still_validates(self, env):
        reg = make_registration(env.db, env.event, "ok@example.com")
        body = env.staff.post(f"{API}/{env.event.id}/validate-qr", json={"qr_code": reg.qr_code}).json()
        assert body["valid"] is True and body["participant_email"] == "ok@example.com"
        assert body["message"].startswith("Valid ticket")

    def test_unknown_qr_is_still_a_valid_false_response_not_an_error(self, env):
        resp = env.staff.post(f"{API}/{env.event.id}/validate-qr", json={"qr_code": "NOPE"})
        assert resp.status_code == 200 and resp.json() == {
            "valid": False, "registration_id": None, "participant_name": None, "participant_email": None,
            "status": None, "event_id": None, "event_title": None, "ticket_type_id": None,
            "message": "QR code not found for this event"}

    def refunded_but_still_confirmed(self, env):
        """Rows refunded before approval started cancelling the registration."""
        ticket = paid_ticket()
        reg = make_registration(env.db, env.event, "refunded@example.com", ticket_type_id=ticket["id"])
        make_order(env.db, env.event, "refunded@example.com", ticket_type_id=ticket["id"], status="refunded", payment_status="refunded")
        return reg

    def test_refunded_registration_cannot_check_in(self, env):
        reg = self.refunded_but_still_confirmed(env)
        resp = env.staff.post(f"{API}/{env.event.id}/check-in", json={"registration_id": str(reg.id)})
        assert resp.status_code == 400 and "refunded" in resp.json()["detail"]
        assert reg_state(env.db, reg)[0] == "confirmed"
        assert audits(env.db, "check_in") == []

    def test_refunded_registration_validates_as_invalid(self, env):
        reg = self.refunded_but_still_confirmed(env)
        body = env.staff.post(f"{API}/{env.event.id}/validate-qr", json={"qr_code": reg.qr_code}).json()
        assert body["valid"] is False and "refunded" in body["message"].lower()

    def test_batch_reports_cancelled_and_refunded_as_failed(self, env):
        cancelled = make_registration(env.db, env.event, "c@example.com", status="cancelled")
        refunded = self.refunded_but_still_confirmed(env)
        good = make_registration(env.db, env.event, "good@example.com")
        resp = env.staff.post(f"{API}/{env.event.id}/batch-check-in", json={"participants": [
            {"registration_id": str(cancelled.id)}, {"registration_id": str(refunded.id)}, {"registration_id": str(good.id)}]})
        assert resp.status_code == 200
        body = resp.json()
        assert (body["total"], body["succeeded"], body["failed"]) == (3, 1, 2)
        assert {r["message"] for r in body["results"] if r["status"] == "failed"} == {"Registration is cancelled", "Registration was refunded"}


class TestCheckOut:
    def test_check_out_before_check_in_is_rejected(self, env):
        reg = make_registration(env.db, env.event, "early@example.com")
        resp = env.staff.post(f"{API}/{env.event.id}/check-out", json={"registration_id": str(reg.id)})
        assert resp.status_code == 400 and "not been checked in" in resp.json()["detail"]
        assert reg_state(env.db, reg)[2] is None
        assert audits(env.db, "check_out") == []

    def test_check_out_after_check_in_works_and_is_idempotent(self, env):
        reg = make_registration(env.db, env.event, "ok@example.com")
        env.staff.post(f"{API}/{env.event.id}/check-in", json={"registration_id": str(reg.id)})
        first = env.staff.post(f"{API}/{env.event.id}/check-out", json={"registration_id": str(reg.id)})
        assert first.status_code == 200 and first.json()["message"] == "Checked out successfully"
        assert set(first.json()) == {"message", "registration_id", "participant_name", "participant_email", "status",
                                     "checked_in_at", "checked_out_at", "session_id"}
        again = env.staff.post(f"{API}/{env.event.id}/check-out", json={"registration_id": str(reg.id)})
        assert again.json()["message"] == "Already checked out"
        assert len(audits(env.db, "check_out")) == 1

    def test_uncheck_in_clears_the_check_out_so_it_cannot_be_reused(self, env):
        reg = make_registration(env.db, env.event, "flip@example.com")
        env.staff.post(f"{API}/{env.event.id}/check-in", json={"registration_id": str(reg.id)})
        env.staff.post(f"{API}/{env.event.id}/check-out", json={"registration_id": str(reg.id)})
        assert reg_state(env.db, reg)[2] is not None
        resp = env.staff.post(f"{API}/{env.event.id}/uncheck-in", json={"registration_id": str(reg.id), "reason": "wrong person"})
        assert resp.status_code == 200 and resp.json()["restored_to"] == "confirmed"
        status, checked_in, checked_out, checked_by, session_id = reg_state(env.db, reg)
        assert (status, checked_in, checked_out, checked_by, session_id) == ("confirmed", None, None, None, None)
        assert audits(env.db, "uncheck_in")[0].notes == "wrong person"
        # ...and a participant who is not checked in cannot be checked out again
        assert env.staff.post(f"{API}/{env.event.id}/check-out", json={"registration_id": str(reg.id)}).status_code == 400


class TestCheckInBehaviour:
    def test_check_in_records_the_operator_and_keeps_its_response_shape(self, env):
        reg = make_registration(env.db, env.event, "guest@example.com")
        resp = env.staff.post(f"{API}/{env.event.id}/check-in", json={"registration_id": str(reg.id), "method": "qr_code"})
        assert resp.status_code == 200
        assert resp.json()["message"] == "Checked in successfully" and resp.json()["status"] == "attended"
        assert set(resp.json()) == {"message", "registration_id", "participant_name", "participant_email", "status", "checked_in_at", "session_id"}
        status, checked_in, _, checked_by, _ = reg_state(env.db, reg)
        assert status == "attended" and checked_in is not None and str(checked_by) == env.owner["id"]

    def test_check_in_by_qr_code_and_duplicate_scan(self, env):
        reg = make_registration(env.db, env.event, "guest@example.com")
        first = env.staff.post(f"{API}/{env.event.id}/check-in", json={"qr_code": reg.qr_code})
        second = env.staff.post(f"{API}/{env.event.id}/check-in", json={"qr_code": reg.qr_code})
        assert first.status_code == second.status_code == 200
        assert second.json()["message"] == "Already checked in"
        assert len(audits(env.db, "check_in")) == 1  # the duplicate scan changed nothing, so it is not re-audited

    def test_qr_from_another_event_does_not_check_in(self, env):
        other = make_event(env.db, env.tenant, env.ent, title="other")
        reg = make_registration(env.db, other, "guest@example.com")
        assert env.staff.post(f"{API}/{env.event.id}/check-in", json={"qr_code": reg.qr_code}).status_code == 404

    def test_session_id_must_belong_to_the_event(self, env):
        reg = make_registration(env.db, env.event, "guest@example.com")
        bad = env.staff.post(f"{API}/{env.event.id}/check-in", json={"registration_id": str(reg.id), "session_id": "not-a-session"})
        assert bad.status_code == 400 and "session" in bad.json()["detail"].lower()
        assert reg_state(env.db, reg)[0] == "confirmed"
        good = env.staff.post(f"{API}/{env.event.id}/check-in", json={"registration_id": str(reg.id), "session_id": "sess-1"})
        assert good.status_code == 200 and good.json()["session_id"] == "sess-1"

    def test_session_of_another_event_is_rejected(self, env):
        other = make_event(env.db, env.tenant, env.ent, sessions=[{"id": "foreign", "title": "x"}])
        reg = make_registration(env.db, env.event, "guest@example.com")
        assert env.staff.post(f"{API}/{env.event.id}/check-in", json={"registration_id": str(reg.id), "session_id": "foreign"}).status_code == 400
        assert other.id != env.event.id

    def test_cross_tenant_staff_cannot_check_in_or_check_out_or_undo(self, env):
        reg = make_registration(env.db, env.event, "guest@example.com")
        attacker = client_for(env.db, staff_user(uuid4(), "admin"))
        for path, body in (("check-in", {"registration_id": str(reg.id)}), ("uncheck-in", {"registration_id": str(reg.id)}),
                           ("check-out", {"registration_id": str(reg.id)}), ("validate-qr", {"qr_code": reg.qr_code}),
                           ("batch-check-in", {"participants": [{"registration_id": str(reg.id)}]})):
            assert attacker.post(f"{API}/{env.event.id}/{path}", json=body).status_code == 403, path
        assert reg_state(env.db, reg)[0] == "confirmed" and audits(env.db) == []

    def test_check_in_is_still_blocked_for_closed_events(self, env):
        env.event.status = "cancelled"
        env.db.commit()
        reg = make_registration(env.db, env.event, "guest@example.com")
        assert env.staff.post(f"{API}/{env.event.id}/check-in", json={"registration_id": str(reg.id)}).status_code == 400


class TestBatchCheckIn:
    def test_batch_records_the_operator_and_audits_each_participant(self, env):
        regs = [make_registration(env.db, env.event, f"p{i}@example.com") for i in range(3)]
        resp = env.staff.post(f"{API}/{env.event.id}/batch-check-in", json={"participants": [{"registration_id": str(r.id)} for r in regs]})
        assert resp.status_code == 200 and resp.json()["succeeded"] == 3
        for reg in regs:
            status, _, _, checked_by, _ = reg_state(env.db, reg)
            assert status == "attended" and str(checked_by) == env.owner["id"]  # was None ("no single user context")
        rows = audits(env.db, "batch_check_in")
        assert len(rows) == 3 and {r.changed_by for r in rows} == {env.owner["id"]}

    def test_batch_is_one_transaction(self, env):
        regs = [make_registration(env.db, env.event, f"p{i}@example.com") for i in range(3)]
        commits = count_commits(env.db)
        env.staff.post(f"{API}/{env.event.id}/batch-check-in", json={"participants": [{"registration_id": str(r.id)} for r in regs]})
        assert len(commits) == 1

    def test_batch_validates_session_per_participant_without_aborting_the_rest(self, env):
        a = make_registration(env.db, env.event, "a@example.com")
        b = make_registration(env.db, env.event, "b@example.com")
        resp = env.staff.post(f"{API}/{env.event.id}/batch-check-in", json={"participants": [
            {"registration_id": str(a.id), "session_id": "bogus"}, {"registration_id": str(b.id), "session_id": "sess-1"}]})
        body = resp.json()
        assert (body["succeeded"], body["failed"]) == (1, 1)
        assert "session" in body["results"][0]["message"].lower()
        assert reg_state(env.db, a)[0] == "confirmed" and reg_state(env.db, b)[0] == "attended"

    def test_batch_already_checked_in_is_reported_as_before(self, env):
        reg = make_registration(env.db, env.event, "a@example.com", status="attended", checked_in_at=datetime.utcnow())
        body = env.staff.post(f"{API}/{env.event.id}/batch-check-in", json={"participants": [{"registration_id": str(reg.id)}]}).json()
        assert body["results"][0]["status"] == "already_checked_in" and body["succeeded"] == 1


# ===========================================================================
# Audit: actor, same-transaction staging, serializability
# ===========================================================================


class TestAuditActor:
    def test_every_phase_2_1_action_records_the_actor(self, env):
        reg = make_registration(env.db, env.event, "guest@example.com")
        staff = env.staff
        staff.post(f"{API}/{env.event.id}/check-in", json={"registration_id": str(reg.id)})
        staff.post(f"{API}/{env.event.id}/check-out", json={"registration_id": str(reg.id)})
        staff.post(f"{API}/{env.event.id}/uncheck-in", json={"registration_id": str(reg.id)})
        staff.post(f"{API}/{env.event.id}/batch-check-in", json={"participants": [{"registration_id": str(reg.id)}]})
        other = make_registration(env.db, env.event, "leaver@example.com")  # attended registrations cannot be cancelled
        staff.delete(f"{API}/{env.event.id}/registrations/{other.id}")
        rows = audits(env.db)
        assert {r.action for r in rows} == {"check_in", "check_out", "uncheck_in", "batch_check_in", "registration_cancel"}
        assert all(r.changed_by == env.owner["id"] for r in rows), [(r.action, r.changed_by) for r in rows]

    def test_refund_and_order_status_actions_record_the_actor(self, env):
        ticket = paid_ticket()
        paid = make_event(env.db, env.tenant, env.ent, pricing_type="paid", price="5", ticket_types=[ticket])
        make_registration(env.db, paid, "buyer@example.com", ticket_type_id=ticket["id"])
        order = make_order(env.db, paid, "buyer@example.com", ticket_type_id=ticket["id"])
        env.staff.post(f"{API}/{paid.id}/orders/{order.id}/refund", json={"reason": "x"})
        env.staff.post(f"{API}/{paid.id}/orders/{order.id}/refund/approve", json={"action": "approve"})
        actions = {r.action: r.changed_by for r in audits(env.db)}
        assert {"refund", "registration_cancel"} <= set(actions)
        assert set(actions.values()) == {env.owner["id"]}
        order2 = make_order(env.db, paid, "second@example.com", ticket_type_id=ticket["id"])
        env.staff.patch(f"{API}/{paid.id}/orders/{order2.id}/status", json={"status": "completed"})
        assert audits(env.db, "order_status")[0].changed_by == env.owner["id"]

    def test_existing_event_lifecycle_audits_now_carry_the_actor(self, env, monkeypatch):
        # create -----------------------------------------------------------------
        monkeypatch.setattr(
            "app.services.event_form_config_service.apply_form_configuration_to_event_data",
            lambda db, data, user: {"tenant_id": env.tenant, "enterprise_id": env.ent.id, "form_configuration_id": None,
                                    "form_configuration_version_id": None, "custom_values": []})
        start = datetime.utcnow() + timedelta(days=5)
        created = env.staff.post(f"{API}/", json={"title": "New", "category": "Wellness", "start_date": start.isoformat(),
                                                  "end_date": (start + timedelta(hours=2)).isoformat(), "status": "draft"})
        assert created.status_code == 201, created.text
        event_id = created.json()["id"]
        # update / status / delete -------------------------------------------------
        assert env.staff.put(f"{API}/{event_id}", json={"title": "Renamed"}).status_code == 200
        assert env.staff.patch(f"{API}/{event_id}/status", json={"status": "cancelled"}).status_code == 200
        assert env.staff.delete(f"{API}/{event_id}").status_code == 200
        by_action = {r.action: r for r in audits(env.db) if str(r.event_id) == event_id}
        assert {"create", "update", "status_change", "delete"} <= set(by_action)
        assert all(r.changed_by == env.owner["id"] for r in by_action.values())

    def test_status_change_audit_carries_actor_and_notes(self, env):
        draft = make_event(env.db, env.tenant, env.ent, status="draft")
        resp = env.staff.patch(f"{API}/{draft.id}/status", json={"status": "cancelled"})
        assert resp.status_code == 200, resp.text
        row = audits(env.db, "status_change")[0]
        assert (row.changed_by, row.before, row.after) == (env.owner["id"], {"status": "draft"}, {"status": "cancelled"})

    def test_update_audit_is_actually_persisted_datetimes_no_longer_break_it(self, env):
        """Regression: before/after contained datetimes, the JSONB insert failed and the row was silently dropped."""
        resp = env.staff.put(f"{API}/{env.event.id}", json={"title": "Changed"})
        assert resp.status_code == 200, resp.text
        row = audits(env.db, "update")[0]
        assert row.before["title"] == "Original title" and row.after["title"] == "Changed"
        assert isinstance(row.before["start_date"], str)  # ISO string, not a datetime object

    def test_system_actions_are_attributed_to_the_system(self, env):
        from app.services.event_service import _log_audit

        _log_audit(env.db, env.event.id, "waitlist_expired", None, {"when": datetime.utcnow(), "who": uuid4()}, changed_by="system:waitlist")
        row = audits(env.db, "waitlist_expired")[0]
        assert row.changed_by == "system:waitlist" and isinstance(row.after["when"], str) and isinstance(row.after["who"], str)


class TestAuditTransactionSafety:
    def test_audit_is_staged_in_the_same_transaction_as_the_change(self, env):
        reg = make_registration(env.db, env.event, "guest@example.com")
        commits = count_commits(env.db)
        assert env.staff.post(f"{API}/{env.event.id}/check-in", json={"registration_id": str(reg.id)}).status_code == 200
        assert len(commits) == 1  # one commit carries both the check-in and its audit row

    @pytest.mark.parametrize("path,body_for", [
        ("check-in", lambda r: {"registration_id": str(r.id)}),
        ("batch-check-in", lambda r: {"participants": [{"registration_id": str(r.id)}]}),
    ])
    def test_a_failed_commit_leaves_neither_the_change_nor_an_orphan_audit_row(self, env, path, body_for):
        reg = make_registration(env.db, env.event, "guest@example.com")
        before_audits = len(audits(env.db))
        original = env.db.commit

        def failing_commit():
            env.db.commit = original
            raise RuntimeError("simulated database outage")

        env.db.commit = failing_commit
        resp = env.staff.post(f"{API}/{env.event.id}/{path}", json=body_for(reg))
        assert resp.status_code == 500
        env.db.rollback()  # what closing the request's session does
        assert reg_state(env.db, reg)[0] == "confirmed"
        assert len(audits(env.db)) == before_audits  # atomic: no audit row without its change

    def test_legacy_commit_shape_never_raises(self, env):
        from unittest.mock import MagicMock

        from app.services.event_service import _log_audit

        broken = MagicMock()
        broken.commit.side_effect = RuntimeError("boom")
        assert _log_audit(broken, env.event.id, "x", None, {"a": 1}) is None
        broken.rollback.assert_called_once()
        staged = MagicMock()
        _log_audit(staged, env.event.id, "x", None, {"a": 1}, commit=False)
        staged.add.assert_called_once()
        staged.commit.assert_not_called()  # commit=False never commits
        staged.flush.assert_not_called()

    def test_a_refund_approval_is_a_single_commit_including_the_cancellation_and_promotion(self, env):
        ticket = paid_ticket()
        paid = make_event(env.db, env.tenant, env.ent, pricing_type="paid", price="5", ticket_types=[ticket], capacity="1")
        make_registration(env.db, paid, "buyer@example.com", ticket_type_id=ticket["id"])
        order = make_order(env.db, paid, "buyer@example.com", ticket_type_id=ticket["id"], status="refund_requested", payment_status="refund_requested")
        commits = count_commits(env.db)
        resp = env.staff.post(f"{API}/{paid.id}/orders/{order.id}/refund/approve", json={"action": "approve"})
        assert resp.status_code == 200
        assert len(commits) == 1
