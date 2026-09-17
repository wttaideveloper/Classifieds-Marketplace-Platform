from datetime import datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import training as routes
from app.core.dependencies import get_current_user
from app.db.database import Base, get_db
from app.models.enterprise_model import Enterprise
from app.models.training_model import Training, TrainingLiveSession, TrainingEnrolment, TrainingProgress, TrainingAssessmentSubmission, TrainingAssignmentSubmission
from app.services.training_service import get_live_attendance_service, export_live_attendance_service
from app.services.training_curriculum import normalize_authoring

TID = UUID('1ceb6f0f-eb69-44b4-b20e-39f3c43acd82')
SID = '4fad8f33-8634-4c0c-af2e-fc75251ee3c9'


@pytest.fixture(params=['lessons','items','standalone'])
def setup(request, monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, 'visit_JSONB', lambda *a, **kw: 'JSON', raising=False)
    engine = create_engine('sqlite://', connect_args={'check_same_thread':False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[m.__table__ for m in (Enterprise,Training,TrainingLiveSession,TrainingEnrolment,TrainingProgress,TrainingAssessmentSubmission,TrainingAssignmentSubmission)])
    sessions=sessionmaker(bind=engine)
    user={'id':str(uuid4()), 'role':'customer', 'email':'learner@example.com'}
    item={'id':SID,'type':'live','title':'Session 1: Understanding Cortisol & the Stress Response','schedule':'18-09-2026 10:00 AM','meeting_link':'https://meet.example.com/session'}
    with sessions() as db:
        db.add(Training(id=TID, enterprise_id=uuid4(), tenant_id=uuid4(), title='Course', category='Wellness', status='published', delivery_mode='online', sections=[] if request.param=='standalone' else [{'id':'section',request.param:[item]}], assessments=[], assignments=[]))
        db.add(TrainingEnrolment(training_id=TID, participant_name='Learner', participant_email=user['email'], status='enrolled'))
        if request.param=='standalone':
            db.add(TrainingLiveSession(id=UUID(SID),training_id=TID,title='Session',scheduled_at=datetime(2026,9,18,10)))
        db.commit()
    app=FastAPI(); app.include_router(routes.router,prefix='/api/v1/trainings')
    def database():
        with sessions() as db: yield db
    app.dependency_overrides[get_db]=database
    app.dependency_overrides[get_current_user]=lambda:user
    with TestClient(app) as client: yield sessions,client,user,request.param
    engine.dispose()


def test_content_id_attendance_round_trip(setup):
    sessions,client,user,kind=setup
    if kind!='standalone':
        content=client.get(f'/api/v1/trainings/{TID}/content')
        assert content.status_code==200,content.text
        assert content.json()['sections'][0]['lessons'][0]['id']==SID
    url=f'/api/v1/trainings/{TID}/live-sessions/{SID}/attendance'
    first=client.post(url)
    assert first.status_code==200,first.text
    second=client.post(url,json={})
    assert second.status_code==200,second.text
    assert first.json()['recorded_at']==second.json()['recorded_at']
    with sessions() as db:
        records=get_live_attendance_service(db,TID,SID)
        assert records['count']==1
        assert records['attendance'][0]['participant_email']==user['email']
        assert user['email'] in export_live_attendance_service(db,TID,SID)[0]
        progress=db.query(TrainingProgress).one()
        assert (f'live:{SID}' if kind=='standalone' else SID) in progress.lessons_completed
        if kind!='standalone':
            training=db.get(Training,TID)
            raw=training.sections[0][kind][0]
            assert raw['attendance']
            normalized=normalize_authoring({'sections':[{'id':'section','lessons':[{'id':SID,'type':'live','title':'Edited'}]}]},training)
            assert normalized['sections'][0]['lessons'][0]['attendance']==raw['attendance']
    if kind!='standalone':
        content=client.get(f'/api/v1/trainings/{TID}/content').json()
        assert content['sections'][0]['lessons'][0]['is_completed'] is True
        assert 'attendance' not in content['sections'][0]['lessons'][0]


def test_attendance_guards(setup):
    sessions,client,user,_=setup
    base=f'/api/v1/trainings/{TID}/live-sessions'
    assert client.post(f'{base}/{uuid4()}/attendance').status_code==404
    assert client.post(f'{base}/invalid/attendance').status_code==400
    assert client.post(f'{base}/{SID}/attendance',json={'participant_email':'other@example.com'}).status_code==403
    with sessions() as db:
        db.query(TrainingEnrolment).one().status='cancelled'; db.commit()
    assert client.post(f'{base}/{SID}/attendance').status_code==403


def test_no_duplicate_post_registration():
    matches=[r for r in routes.router.routes if r.path=='/{training_id}/live-sessions/{session_id}/attendance' and 'POST' in r.methods]
    assert len(matches)==1


def test_non_live_and_wrong_training_ids_rejected(setup):
    sessions,client,user,kind=setup
    wrong_id=uuid4()
    with sessions() as db:
        db.add(Training(id=wrong_id,enterprise_id=uuid4(),title='Other',category='General',sections=[],assessments=[],assignments=[]))
        if kind!='standalone':
            row=db.get(Training,TID)
            row.sections=[{'id':'section','lessons':[{'id':SID,'type':'video','title':'Not a session'}]}]
        db.commit()
    assert client.post(f'/api/v1/trainings/{wrong_id}/live-sessions/{SID}/attendance').status_code==404
    if kind!='standalone':
        assert client.post(f'/api/v1/trainings/{TID}/live-sessions/{SID}/attendance').status_code==404


def test_legacy_email_keyed_attendance(setup):
    sessions,client,user,kind=setup
    if kind!='standalone':
        return
    with sessions() as db:
        db.get(TrainingLiveSession,UUID(SID)).attendance={user['email']:{'joined_at':'2026-09-18T10:00:00','name':'Learner'}}
        db.commit()
    response=client.post(f'/api/v1/trainings/{TID}/live-sessions/{SID}/attendance')
    assert response.status_code==200,response.text
    assert response.json()['recorded_at']=='2026-09-18T10:00:00'
    with sessions() as db:
        assert get_live_attendance_service(db,TID,SID)['count']==1
        assert user['email'] in export_live_attendance_service(db,TID,SID)[0]


def test_is_attended_in_content_api(setup):
    sessions, client, user, kind = setup
    if kind == 'standalone':
        return  # Standalone sessions are not returned in the curriculum content API directly

    # TEST 1: Before attendance
    content_before = client.get(f'/api/v1/trainings/{TID}/content')
    assert content_before.status_code == 200
    lesson = content_before.json()['sections'][0]['lessons'][0]
    assert lesson['is_attended'] is False
    assert lesson['attended_at'] is None
    assert lesson['is_completed'] is False

    # POST attendance
    url = f'/api/v1/trainings/{TID}/live-sessions/{SID}/attendance'
    post_resp = client.post(url)
    assert post_resp.status_code == 200
    recorded_at = post_resp.json()['recorded_at']

    # TEST 2: After attendance
    content_after = client.get(f'/api/v1/trainings/{TID}/content')
    lesson_after = content_after.json()['sections'][0]['lessons'][0]
    assert lesson_after['is_attended'] is True
    assert lesson_after['attended_at'] == recorded_at
    # Note: is_completed becomes True due to progress side-effect in record_live_attendance_service
    assert lesson_after['is_completed'] is True
    assert 'attendance' not in lesson_after

    # TEST 3: User isolation
    # Authenticate as another user
    from app.core.dependencies import get_current_user
    user_b = {'id': str(uuid4()), 'role': 'customer', 'email': 'user_b@example.com'}
    client.app.dependency_overrides[get_current_user] = lambda: user_b

    # Enrol User B
    from app.models.training_model import TrainingEnrolment
    with sessions() as db:
        db.add(TrainingEnrolment(training_id=TID, participant_name='User B', participant_email=user_b['email'], status='enrolled'))
        db.commit()

    content_b = client.get(f'/api/v1/trainings/{TID}/content')
    lesson_b = content_b.json()['sections'][0]['lessons'][0]
    assert lesson_b['is_attended'] is False
    assert lesson_b['attended_at'] is None
    # Restore override
    client.app.dependency_overrides[get_current_user] = lambda: user

def test_non_live_lesson_attendance(setup):
    sessions, client, user, kind = setup
    if kind == 'standalone':
        return
    with sessions() as db:
        training = db.get(Training, TID)
        training.sections = [{'id': 'section', 'lessons': [{'id': str(uuid4()), 'type': 'video', 'title': 'Video'}]}]
        db.commit()

    content = client.get(f'/api/v1/trainings/{TID}/content')
    lesson = content.json()['sections'][0]['lessons'][0]
    assert lesson.get('is_attended') is None
    assert lesson.get('attended_at') is None
