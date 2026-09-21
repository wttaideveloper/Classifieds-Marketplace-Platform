"""HTTP authorization and real database regressions for the Training audit."""
import csv
import io
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import training as routes
from app.core.dependencies import get_current_user
from app.db.database import Base, get_db
from app.models.enterprise_model import Enterprise
from app.models.training_model import Training, TrainingEnrolment, TrainingLiveSession, TrainingOrder, TrainingReview, TrainingWaitlist
from app.repository.training_repo import require_training_owner
from app.services import training_service as service


@pytest.fixture
def audit(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[Enterprise.__table__, Training.__table__, TrainingEnrolment.__table__, TrainingOrder.__table__, TrainingWaitlist.__table__, TrainingLiveSession.__table__, TrainingReview.__table__])
    sessions = sessionmaker(bind=engine)
    tenant, other = uuid4(), uuid4()
    tid, foreign = uuid4(), uuid4()
    with sessions() as db:
        for training_id, owner in ((tid, tenant), (foreign, other)):
            db.add(Training(id=training_id, tenant_id=owner, enterprise_id=uuid4(), title="Course", category="General",
                delivery_mode="self_paced", status="published", price="100", currency="INR", capacity="2",
                sections=[{"id":"s1", "title":"Section", "type":"section", "lessons":[{"id":"l1", "title":"Lesson", "type":"topic", "videos":["https://example.com/video"]}]}],
                assessments=[], assignments=[], moderation_history=[]))
        db.commit()
    user = {"role":"provider", "tenant_id":str(tenant), "email":"owner@example.com"}
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1/trainings")
    app.include_router(routes.router, prefix="/api/v1/courses")
    def database():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        yield sessions, client, user, tid, foreign
    engine.dispose()


MUTATIONS = [
    ("put", "", {"title":"Changed"}),
    ("post", "/sections", {"title":"Added", "type":"section"}),
    ("put", "/sections/s1", {"title":"Changed", "type":"section"}),
    ("delete", "/sections/s1", None),
    ("post", "/sections/s1/lessons", {"title":"Added", "type":"topic"}),
    ("put", "/sections/s1/lessons/l1", {"title":"Changed", "type":"topic"}),
    ("delete", "/sections/s1/lessons/l1", None),
    ("delete", "/sections/s1/lessons/l1/media?kind=videos&url=https://example.com/video", None),
    ("post", "/sections/reorder", {"section_ids":["s1"]}),
    ("post", "/modules/reorder", {"module_ids":["s1"]}),
    ("post", "/sections/s1/lessons/reorder", {"lesson_ids":["l1"]}),
    ("post", "/sections/s1/lessons/l1/topics", {"title":"Topic"}),
]


@pytest.mark.parametrize("method,path,payload", MUTATIONS)
@pytest.mark.parametrize("prefix", ["trainings", "courses"])
@pytest.mark.parametrize("role", ["admin", "provider"])
def test_cross_tenant_mutations_rejected(audit, method, path, payload, prefix, role):
    sessions, client, user, tid, foreign = audit
    user["role"] = role
    response = client.request(method, f"/api/v1/{prefix}/{foreign}{path}", **({"json":payload} if payload is not None else {}))
    assert response.status_code == 403, response.text
    with sessions() as db:
        assert db.get(Training, foreign).sections[0]["title"] == "Section"


@pytest.mark.parametrize("role", ["admin", "provider", "super_admin"])
@pytest.mark.parametrize("method,path,payload", MUTATIONS[1:8])
def test_authorized_curriculum_mutations(audit, role, method, path, payload):
    _, client, user, tid, foreign = audit
    user["role"] = role
    target = foreign if role == "super_admin" else tid
    response = client.request(method, f"/api/v1/trainings/{target}{path}", **({"json":payload} if payload is not None else {}))
    assert response.status_code in (200, 201), response.text


def test_missing_tenant_denied(audit):
    _, client, user, tid, _ = audit
    user.pop("tenant_id")
    assert client.put(f"/api/v1/trainings/{tid}/sections/s1", json={"title":"No"}).status_code == 403


def test_nested_ids_cannot_access_foreign_training(audit):
    sessions, client, _, tid, foreign = audit
    with sessions() as db:
        row = db.get(Training, foreign)
        row.sections = [{"id":"foreign-section", "lessons":[{"id":"foreign-lesson"}]}]
        db.commit()
    response = client.put(f"/api/v1/trainings/{tid}/sections/foreign-section/lessons/foreign-lesson", json={"title":"No"})
    assert response.status_code == 404


def test_export_csv_is_scoped_and_downloadable(audit):
    sessions, client, _, tid, foreign = audit
    with sessions() as db:
        db.add_all([TrainingEnrolment(training_id=tid, participant_name='Alex, A', participant_email='a@example.com'),
                    TrainingEnrolment(training_id=foreign, participant_name='Secret', participant_email='secret@example.com')])
        db.commit()
    response = client.get(f"/api/v1/trainings/{tid}/enrolments/export")
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/csv')
    assert f'training_{tid}_enrolments.csv' in response.headers['content-disposition']
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 1 and rows[0]['participant_name'] == 'Alex, A'
    assert 'secret@example.com' not in response.text
    assert client.get(f"/api/v1/trainings/{foreign}/enrolments/export").status_code == 403


def test_empty_export_has_headers(audit):
    _, client, _, tid, _ = audit
    response = client.get(f"/api/v1/trainings/{tid}/enrolments/export")
    assert response.status_code == 200
    reader = csv.DictReader(io.StringIO(response.text))
    assert 'participant_email' in reader.fieldnames
    assert list(reader) == []


PAYLOAD = {"participant_name":"Learner", "participant_email":"learner@example.com"}


def test_checkout_and_normal_enrol_share_capacity_and_duplicate_rules(audit):
    sessions, _, _, tid, _ = audit
    with sessions() as db:
        order = service.create_training_checkout_service(db, tid, PAYLOAD)
        assert order.status == 'confirmed'
        assert db.query(TrainingEnrolment).one().qr_code
        with pytest.raises(HTTPException, match='Already enrolled'):
            service.create_training_checkout_service(db, tid, PAYLOAD)
        service.create_training_enrol_service(db, tid, {**PAYLOAD, 'participant_email':'second@example.com'})
        for operation in (service.create_training_checkout_service, service.create_training_enrol_service):
            with pytest.raises(HTTPException, match='at capacity'):
                operation(db, tid, {**PAYLOAD, 'participant_email':'third@example.com'})
        assert db.query(TrainingOrder).count() == 1
        assert db.query(TrainingEnrolment).count() == 2


@pytest.mark.parametrize('failed_model', [TrainingEnrolment, TrainingOrder])
def test_checkout_failures_roll_back_enrolment_and_order(audit, failed_model):
    sessions, _, _, tid, _ = audit
    def fail(mapper, connection, target):
        raise RuntimeError('insert failed')
    event.listen(failed_model, 'before_insert', fail)
    try:
        with sessions() as db:
            with pytest.raises(RuntimeError, match='insert failed'):
                service.create_training_checkout_service(db, tid, PAYLOAD)
    finally:
        event.remove(failed_model, 'before_insert', fail)
    with sessions() as db:
        assert db.query(TrainingOrder).count() == 0
        assert db.query(TrainingEnrolment).count() == 0


@pytest.mark.parametrize('before,after', [('pending_approval','approved'), ('pending_approval','rejected'), ('pending_approval','needs_revision'), ('approved','published')])
def test_moderation_history_survives_reload(audit, before, after):
    sessions, _, user, tid, _ = audit
    with sessions() as db:
        db.get(Training, tid).status = before
        db.commit()
        service.update_training_status_service(db, tid, after, user, 'Review note')
    with sessions() as db:
        entry = db.get(Training, tid).moderation_history[-1]
        assert entry['previous_status'] == before and entry['new_status'] == after
        assert entry['reason'] == 'Review note' and entry['actor_email'] == user['email']


def test_invalid_moderation_does_not_record_history(audit):
    sessions, _, user, tid, _ = audit
    with sessions() as db:
        db.get(Training, tid).status = 'draft'
        db.commit()
        with pytest.raises(HTTPException):
            service.update_training_status_service(db, tid, 'published', user)
        assert db.get(Training, tid).moderation_history == []


def test_legacy_training_and_enterprise_identity_fallback(audit):
    sessions, client, user, tid, foreign = audit
    with sessions() as db:
        row = db.get(Training, tid)
        enterprise_id = row.enterprise_id
        db.add(Enterprise(id=enterprise_id, tenant_id=row.tenant_id, business_short_name='Owner',
            business_legal_name='Owner Ltd', business_email='owner@example.com'))
        row.tenant_id = None
        db.commit()
    user.pop('tenant_id')
    user['enterprise_id'] = str(enterprise_id)
    assert client.put(f'/api/v1/trainings/{tid}/sections/s1', json={'title':'Updated'}).status_code == 200
    assert client.put(f'/api/v1/trainings/{foreign}/sections/s1', json={'title':'No'}).status_code == 403


def test_cookie_tenant_resolution_keeps_export_authorized(audit, monkeypatch):
    from app.services import invigorate_auth_client
    sessions, client, user, tid, _ = audit
    tenant_id = user.pop('tenant_id')
    monkeypatch.setattr(routes, 'get_web_session_cookie_token', lambda request: 'test-session')
    monkeypatch.setattr(invigorate_auth_client, 'fetch_tenant_me_profile', lambda token: {'id':tenant_id})
    assert client.get(f'/api/v1/trainings/{tid}/enrolments/export').status_code == 200


def test_export_service_rejects_cross_tenant_without_route(audit):
    sessions, _, user, _, foreign = audit
    with sessions() as db, pytest.raises(HTTPException) as exc:
        service.export_training_enrolments_service(db, foreign, user)
    assert exc.value.status_code == 403


def test_checkout_http_surfaces_full_training(audit):
    sessions, client, _, tid, _ = audit
    with sessions() as db:
        db.get(Training, tid).capacity = '1'
        db.commit()
    assert client.post(f'/api/v1/trainings/{tid}/checkout', json=PAYLOAD).status_code == 201
    response = client.post(f'/api/v1/trainings/{tid}/checkout', json={**PAYLOAD, 'participant_email':'second@example.com'})
    assert response.status_code == 400 and 'at capacity' in response.json()['detail']


# --- A. Cross-tenant enrolment cancellation (cancel_enrol IDOR) ---

@pytest.mark.parametrize('role', ['admin', 'provider', 'super_admin'])
def test_staff_cancel_enrol_same_tenant_allowed(audit, role):
    sessions, client, user, tid, _ = audit
    user['role'] = role
    with sessions() as db:
        enrol = TrainingEnrolment(training_id=tid, participant_name='Learner', participant_email='learner@example.com', status='enrolled')
        db.add(enrol); db.commit(); db.refresh(enrol)
        enrol_id = enrol.id
    response = client.delete(f'/api/v1/trainings/{tid}/enrolments/{enrol_id}')
    assert response.status_code == 200, response.text
    with sessions() as db:
        assert db.get(TrainingEnrolment, enrol_id).status == 'cancelled'


@pytest.mark.parametrize('role', ['admin', 'provider'])
def test_staff_cancel_enrol_cross_tenant_rejected(audit, role):
    """Reproduces the reported IDOR: a Tenant A admin/provider must not be
    able to cancel a Tenant B enrolment just by knowing its id."""
    sessions, client, user, _, foreign = audit
    user['role'] = role
    with sessions() as db:
        enrol = TrainingEnrolment(training_id=foreign, participant_name='Learner', participant_email='learner@example.com', status='enrolled')
        db.add(enrol); db.commit(); db.refresh(enrol)
        enrol_id = enrol.id
    response = client.delete(f'/api/v1/trainings/{foreign}/enrolments/{enrol_id}')
    assert response.status_code == 403, response.text
    with sessions() as db:
        assert db.get(TrainingEnrolment, enrol_id).status == 'enrolled'


def test_super_admin_cancel_enrol_cross_tenant_still_allowed(audit):
    """Existing Super Admin cross-tenant management behavior is preserved."""
    sessions, client, user, _, foreign = audit
    user['role'] = 'super_admin'
    with sessions() as db:
        enrol = TrainingEnrolment(training_id=foreign, participant_name='Learner', participant_email='learner@example.com', status='enrolled')
        db.add(enrol); db.commit(); db.refresh(enrol)
        enrol_id = enrol.id
    response = client.delete(f'/api/v1/trainings/{foreign}/enrolments/{enrol_id}')
    assert response.status_code == 200, response.text


def test_learner_can_still_cancel_own_enrolment(audit):
    sessions, client, user, tid, _ = audit
    user['role'] = 'participant'
    user['email'] = 'learner@example.com'
    with sessions() as db:
        enrol = TrainingEnrolment(training_id=tid, participant_name='Learner', participant_email='learner@example.com', status='enrolled')
        db.add(enrol); db.commit(); db.refresh(enrol)
        enrol_id = enrol.id
    assert client.delete(f'/api/v1/trainings/{tid}/enrolments/{enrol_id}').status_code == 200


def test_learner_cannot_cancel_someone_elses_enrolment(audit):
    sessions, client, user, tid, _ = audit
    user['role'] = 'participant'
    user['email'] = 'learner@example.com'
    with sessions() as db:
        enrol = TrainingEnrolment(training_id=tid, participant_name='Other', participant_email='other@example.com', status='enrolled')
        db.add(enrol); db.commit(); db.refresh(enrol)
        enrol_id = enrol.id
    assert client.delete(f'/api/v1/trainings/{tid}/enrolments/{enrol_id}').status_code == 404


def test_foreign_training_enrol_id_pair_cannot_be_used_against_own_tenant(audit):
    """A Tenant A admin cannot reach a Tenant B enrolment by pairing its id
    with a Tenant A training_id — the service's own training_id==tid filter
    rejects the mismatched pair before the tenant check even applies."""
    sessions, client, user, tid, foreign = audit
    user['role'] = 'admin'
    with sessions() as db:
        enrol = TrainingEnrolment(training_id=foreign, participant_name='Learner', participant_email='learner@example.com', status='enrolled')
        db.add(enrol); db.commit(); db.refresh(enrol)
        enrol_id = enrol.id
    response = client.delete(f'/api/v1/trainings/{tid}/enrolments/{enrol_id}')
    assert response.status_code == 404, response.text


# --- B. Cross-tenant waitlist removal (leave_waitlist IDOR) ---

@pytest.mark.parametrize('role', ['admin', 'provider', 'super_admin'])
def test_staff_leave_waitlist_same_tenant_allowed(audit, role):
    sessions, client, user, tid, _ = audit
    user['role'] = role
    with sessions() as db:
        entry = TrainingWaitlist(training_id=tid, participant_name='Learner', participant_email='learner@example.com')
        db.add(entry); db.commit(); db.refresh(entry)
        entry_id = entry.id
    response = client.delete(f'/api/v1/trainings/{tid}/waitlist/{entry_id}')
    assert response.status_code == 200, response.text
    with sessions() as db:
        assert db.get(TrainingWaitlist, entry_id) is None


@pytest.mark.parametrize('role', ['admin', 'provider'])
def test_staff_leave_waitlist_cross_tenant_rejected(audit, role):
    sessions, client, user, _, foreign = audit
    user['role'] = role
    with sessions() as db:
        entry = TrainingWaitlist(training_id=foreign, participant_name='Learner', participant_email='learner@example.com')
        db.add(entry); db.commit(); db.refresh(entry)
        entry_id = entry.id
    response = client.delete(f'/api/v1/trainings/{foreign}/waitlist/{entry_id}')
    assert response.status_code == 403, response.text
    with sessions() as db:
        assert db.get(TrainingWaitlist, entry_id) is not None


def test_learner_can_still_leave_own_waitlist(audit):
    sessions, client, user, tid, _ = audit
    user['role'] = 'participant'
    user['email'] = 'learner@example.com'
    with sessions() as db:
        entry = TrainingWaitlist(training_id=tid, participant_name='Learner', participant_email='learner@example.com')
        db.add(entry); db.commit(); db.refresh(entry)
        entry_id = entry.id
    assert client.delete(f'/api/v1/trainings/{tid}/waitlist/{entry_id}').status_code == 200


# --- C. PATCH /status records actor + reason through the HTTP route ---

@pytest.mark.parametrize('before,after', [('pending_approval','approved'), ('pending_approval','rejected'), ('pending_approval','needs_revision'), ('approved','published')])
def test_patch_status_route_records_actor_and_reason(audit, before, after):
    sessions, client, user, tid, _ = audit
    user['role'] = 'admin'
    user['id'] = '11111111-1111-1111-1111-111111111111'
    with sessions() as db:
        row = db.get(Training, tid)
        row.status = before
        row.moderation_history = []
        db.commit()
    response = client.patch(f'/api/v1/trainings/{tid}/status', json={'status': after, 'reason': 'Reviewed by QA'})
    assert response.status_code == 200, response.text
    with sessions() as db:
        entry = db.get(Training, tid).moderation_history[-1]
    assert entry['previous_status'] == before
    assert entry['new_status'] == after
    assert entry['reason'] == 'Reviewed by QA'
    assert entry['actor_email'] == 'owner@example.com'
    assert entry['actor_role'] == 'admin'
    assert entry['actor_id'] == user['id']


def test_patch_status_draft_to_published_remains_blocked(audit):
    sessions, client, user, tid, _ = audit
    user['role'] = 'admin'
    with sessions() as db:
        row = db.get(Training, tid)
        row.status = 'draft'
        row.moderation_history = []
        db.commit()
    response = client.patch(f'/api/v1/trainings/{tid}/status', json={'status': 'published'})
    assert response.status_code == 400, response.text
    with sessions() as db:
        assert db.get(Training, tid).moderation_history == []


# --- D/E. Suspend/cancel record exactly one moderation history entry ---

@pytest.mark.parametrize('endpoint,new_status', [('suspend', 'suspended'), ('cancel', 'cancelled')])
@pytest.mark.parametrize('reason', ['Policy violation', None])
def test_suspend_and_cancel_record_exactly_one_history_entry(audit, endpoint, new_status, reason):
    sessions, client, user, tid, _ = audit
    user['role'] = 'admin'
    user['id'] = '22222222-2222-2222-2222-222222222222'
    with sessions() as db:
        row = db.get(Training, tid)
        row.status = 'published'
        row.moderation_history = []
        db.commit()
    payload = {'status': new_status, 'reason': reason} if reason else None
    response = client.post(f'/api/v1/trainings/{tid}/{endpoint}', **({'json': payload} if payload else {}))
    assert response.status_code == 200, response.text
    with sessions() as db:
        history = db.get(Training, tid).moderation_history
    assert len(history) == 1, history
    entry = history[0]
    assert entry['previous_status'] == 'published'
    assert entry['new_status'] == new_status
    assert entry['reason'] == reason
    assert entry['actor_email'] == 'owner@example.com'
    assert entry['actor_role'] == 'admin'
    assert entry['actor_id'] == user['id']


# --- /enroll response context (training_title, media, payment, next_session) ---

def test_enroll_response_includes_training_and_payment_context(audit):
    sessions, client, user, tid, _ = audit
    user['role'] = 'participant'
    user['email'] = 'learner@example.com'
    response = client.post(f'/api/v1/trainings/{tid}/enroll', json={'participant_email': 'learner@example.com', 'participant_name': 'Learner'})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body['training_title'] == 'Course'
    assert body['delivery_mode'] == 'self_paced'
    assert body['currency'] == 'INR'
    assert body['amount_paid'] == '100'  # no order yet — falls back to the training's list price
    assert body['payment_status'] == 'pending'
    assert body['message'] == 'Enrolled successfully'
    assert body['enterprise_name'] is None  # fixture training has no persisted Enterprise row
    assert body['next_session'] is None  # no live session scheduled


def test_enroll_response_prefers_matching_order_for_payment_fields(audit):
    from app.models.training_model import TrainingOrder
    sessions, client, user, tid, _ = audit
    user['role'] = 'participant'
    user['email'] = 'learner@example.com'
    with sessions() as db:
        db.add(TrainingOrder(training_id=tid, participant_name='Learner', participant_email='learner@example.com',
            amount='80', currency='USD', payment_status='confirmed', status='confirmed'))
        db.commit()
    response = client.post(f'/api/v1/trainings/{tid}/enroll', json={'participant_email': 'learner@example.com', 'participant_name': 'Learner'})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body['amount_paid'] == '80'
    assert body['currency'] == 'USD'
    assert body['payment_status'] == 'confirmed'


def test_enroll_response_includes_next_scheduled_session(audit):
    from datetime import datetime, timedelta
    from app.models.training_model import TrainingLiveSession
    sessions, client, user, tid, _ = audit
    user['role'] = 'participant'
    user['email'] = 'learner@example.com'
    with sessions() as db:
        db.add(TrainingLiveSession(training_id=tid, title='Live Q&A', scheduled_at=datetime.utcnow() + timedelta(days=3), meeting_link='https://zoom.example/abc'))
        db.commit()
    response = client.post(f'/api/v1/trainings/{tid}/enroll', json={'participant_email': 'learner@example.com', 'participant_name': 'Learner'})
    assert response.status_code == 201, response.text
    next_session = response.json()['next_session']
    assert next_session is not None
    assert next_session['meeting_link'] == 'https://zoom.example/abc'
    assert next_session['schedule'] is not None


# --- GET /reports/summary: tenant-scoped, new fields ---

def test_reports_summary_scoped_to_caller_tenant_not_client_input(audit):
    """The reported bug: enterprise_id used to be an arbitrary client query
    param. It must now be resolved server-side from the caller's own tenant,
    with no way to see another tenant's portfolio — whether by omission or
    by explicitly passing a foreign enterprise_id (now not even accepted)."""
    sessions, client, user, tid, foreign = audit
    with sessions() as db:
        db.add(TrainingEnrolment(training_id=tid, participant_name='A', participant_email='a@example.com', status='enrolled'))
        db.add(TrainingEnrolment(training_id=foreign, participant_name='B', participant_email='b@example.com', status='enrolled'))
        db.commit()

    response = client.get('/api/v1/trainings/reports/summary')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['total_trainings'] == 1  # only tid, not foreign
    assert body['total_registrations'] == 1

    # Explicitly trying to request the foreign tenant's data must not work.
    spoofed = client.get(f'/api/v1/trainings/reports/summary?enterprise_id={foreign}')
    assert spoofed.status_code == 200
    assert spoofed.json()['total_trainings'] == 1  # unchanged — the param is not accepted


def test_reports_summary_super_admin_sees_all_tenants(audit):
    sessions, client, user, tid, foreign = audit
    user['role'] = 'super_admin'
    response = client.get('/api/v1/trainings/reports/summary')
    assert response.status_code == 200, response.text
    assert response.json()['total_trainings'] == 2


def test_reports_summary_learner_forbidden(audit):
    sessions, client, user, tid, _ = audit
    user['role'] = 'participant'
    assert client.get('/api/v1/trainings/reports/summary').status_code == 403


def test_reports_summary_new_fields(audit):
    from datetime import datetime, timedelta
    sessions, client, user, tid, _ = audit
    with sessions() as db:
        row = db.get(Training, tid)
        row.start_date = datetime.utcnow() + timedelta(days=5)
        db.add(TrainingEnrolment(training_id=tid, participant_name='A', participant_email='a@example.com', status='attended'))
        db.add(TrainingEnrolment(training_id=tid, participant_name='B', participant_email='b@example.com', status='cancelled'))
        db.commit()

    response = client.get('/api/v1/trainings/reports/summary')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['upcoming_trainings'] == 1
    assert body['past_trainings'] == 0
    assert body['total_registrations'] == 1  # attended counts, cancelled does not
    assert body['total_attended'] == 1
    assert body['average_rating'] is None  # no reviews yet
    for key in ('by_status', 'by_category', 'by_delivery_mode'):
        assert key in body
