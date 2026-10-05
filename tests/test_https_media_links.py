"""Training APIs return HTTPS links only.

Mobile blocks cleartext http:// media, and uploads were stored with absolute URLs built from
PUBLIC_API_BASE_URL while that was http://13.207.85.164 — e.g. Live demo's
http://13.207.85.164/api/v1/trainings/upload/b7c6fa47-...png. The fix rewrites on read (repairs rows
already stored) and normalizes on write.
"""
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import training as training_routes
from app.core.config import settings
from app.core.dependencies import get_current_user
from app.core.https_links import HttpsLinkRewriteMiddleware
from app.db.database import Base, get_db
from app.models.enterprise_model import Enterprise
from app.models.training_model import (
    Training, TrainingAssessmentSubmission, TrainingAssignmentSubmission, TrainingEnrolment,
    TrainingLessonAttendance, TrainingLiveSession, TrainingOrder, TrainingProgress, TrainingReview,
    TrainingWaitlist,
)
from app.services import training_upload_service
from app.services.training_curriculum import normalize_authoring
from app.utils.public_urls import deep_https, to_https

CANONICAL = "https://chat.wisdomtooth.tech"
OLD_IP = "http://13.207.85.164"
UPLOAD_PATH = "/api/v1/trainings/upload/b7c6fa47-1111-2222-3333-444455556666_live-demo.png"


@pytest.fixture(autouse=True)
def https_on(monkeypatch):
    monkeypatch.setattr(settings, "FORCE_HTTPS_MEDIA_URLS", True)
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", CANONICAL)
    monkeypatch.setattr(settings, "PUBLIC_MEDIA_BASE_URL", "")


# --- the rewrite rules ---------------------------------------------------------------------

def test_the_reported_bare_ip_upload_link_becomes_the_https_host():
    assert to_https(OLD_IP + UPLOAD_PATH) == CANONICAL + UPLOAD_PATH


def test_query_string_and_fragment_survive():
    assert to_https(f"{OLD_IP}/api/v1/trainings/upload/a.pdf?x=1#p2") == f"{CANONICAL}/api/v1/trainings/upload/a.pdf?x=1#p2"


@pytest.mark.parametrize(("given", "expected"), [
    ("http://cdn.example.com/a.png", "https://cdn.example.com/a.png"),
    ("http://cdn.example.com:80/a.png", "https://cdn.example.com/a.png"),
    ("http://cdn.example.com:8080/a.png", "https://cdn.example.com:8080/a.png"),
    ("HTTP://cdn.example.com/a.png", "https://cdn.example.com/a.png"),
])
def test_other_hosts_keep_their_host_and_gain_https(given, expected):
    assert to_https(given) == expected


@pytest.mark.parametrize("untouched", [
    "https://cdn.example.com/a.png",
    "/api/v1/trainings/upload/a.png",
    "mailto:a@b.co",
    "data:image/png;base64,AAAA",
    "http://localhost:8000/a.png",
    "see http://example.com for details",   # a URL inside text is not a link value
    "http://example.com/a b.png",           # whitespace -> not a single URL
    "",
    None,
    42,
])
def test_values_that_are_not_plain_http_links_are_left_alone(untouched):
    assert to_https(untouched) == untouched


def test_an_unrelated_bare_ip_path_is_not_pointed_at_our_host():
    assert to_https("http://13.207.85.164/other/thing.png") == "http://13.207.85.164/other/thing.png"


def test_without_an_https_hostname_a_bare_ip_link_is_left_as_stored(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", OLD_IP)  # nothing better to point at
    assert to_https(OLD_IP + UPLOAD_PATH) == OLD_IP + UPLOAD_PATH  # never https://<ip>


def test_a_non_ip_http_base_is_upgraded_and_used_as_the_canonical_host(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "http://chat.wisdomtooth.tech")
    assert to_https(OLD_IP + UPLOAD_PATH) == CANONICAL + UPLOAD_PATH


def test_public_media_base_url_wins_over_public_api_base_url(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_MEDIA_BASE_URL", "https://media.example.com")
    assert to_https(OLD_IP + UPLOAD_PATH) == "https://media.example.com" + UPLOAD_PATH


def test_nothing_changes_when_the_switch_is_off(monkeypatch):
    monkeypatch.setattr(settings, "FORCE_HTTPS_MEDIA_URLS", False)
    assert to_https(OLD_IP + UPLOAD_PATH) == OLD_IP + UPLOAD_PATH
    data = {"a": OLD_IP + UPLOAD_PATH}
    assert deep_https(data) is False and data == {"a": OLD_IP + UPLOAD_PATH}


def test_deep_rewrite_reaches_nested_values_and_reports_changes():
    data = {
        "primary_image": OLD_IP + UPLOAD_PATH,
        "gallery_images": [OLD_IP + UPLOAD_PATH, "https://cdn.example.com/ok.png"],
        "sections": [{"lessons": [{"documents": [{"url": OLD_IP + UPLOAD_PATH, "name": "n"}], "videos": [OLD_IP + UPLOAD_PATH]}]}],
        "description": "visit http://example.com today",
        "count": 3,
    }
    assert deep_https(data) is True
    assert data["primary_image"] == CANONICAL + UPLOAD_PATH
    assert data["gallery_images"] == [CANONICAL + UPLOAD_PATH, "https://cdn.example.com/ok.png"]
    assert data["sections"][0]["lessons"][0]["documents"][0]["url"] == CANONICAL + UPLOAD_PATH
    assert data["sections"][0]["lessons"][0]["videos"] == [CANONICAL + UPLOAD_PATH]
    assert data["description"] == "visit http://example.com today" and data["count"] == 3
    assert deep_https(data) is False  # idempotent


# --- the response middleware ---------------------------------------------------------------

@pytest.fixture
def mw_client():
    app = FastAPI()
    app.add_middleware(HttpsLinkRewriteMiddleware)

    @app.get("/api/v1/trainings/x")
    def training():
        return {"primary_image": OLD_IP + UPLOAD_PATH, "title": "Live demo"}

    @app.get("/api/v1/admin/courses/x")
    def course():
        return {"primary_image": OLD_IP + UPLOAD_PATH}

    @app.get("/api/v1/events/x")
    def event():
        return {"banner": OLD_IP + UPLOAD_PATH}

    @app.get("/api/v1/trainings/clean")
    def clean():
        return JSONResponse({"title": "é – ok", "n": 1.5})

    @app.get("/api/v1/trainings/text")
    def text():
        return PlainTextResponse(OLD_IP + UPLOAD_PATH)

    return TestClient(app)


def test_training_json_is_rewritten_with_a_correct_content_length(mw_client):
    resp = mw_client.get("/api/v1/trainings/x")
    assert resp.json() == {"primary_image": CANONICAL + UPLOAD_PATH, "title": "Live demo"}
    assert int(resp.headers["content-length"]) == len(resp.content)
    assert resp.headers["content-type"].startswith("application/json")


def test_course_alias_routes_are_covered_too(mw_client):
    assert mw_client.get("/api/v1/admin/courses/x").json()["primary_image"] == CANONICAL + UPLOAD_PATH


def test_other_modules_are_not_touched(mw_client):
    assert mw_client.get("/api/v1/events/x").json()["banner"] == OLD_IP + UPLOAD_PATH


def test_non_json_responses_pass_through_untouched(mw_client):
    assert mw_client.get("/api/v1/trainings/text").text == OLD_IP + UPLOAD_PATH


def test_a_response_with_nothing_to_rewrite_is_byte_for_byte_identical(mw_client):
    raw = TestClient(_plain_app()).get("/api/v1/trainings/clean").content
    assert mw_client.get("/api/v1/trainings/clean").content == raw


def _plain_app():
    app = FastAPI()

    @app.get("/api/v1/trainings/clean")
    def clean():
        return JSONResponse({"title": "é – ok", "n": 1.5})

    return app


def test_the_middleware_does_nothing_when_https_only_is_off(mw_client, monkeypatch):
    monkeypatch.setattr(settings, "FORCE_HTTPS_MEDIA_URLS", False)
    assert mw_client.get("/api/v1/trainings/x").json()["primary_image"] == OLD_IP + UPLOAD_PATH


def test_the_real_application_has_the_middleware_installed():
    from app.main import app

    assert HttpsLinkRewriteMiddleware in [m.cls for m in app.user_middleware]


# --- the real Training APIs ----------------------------------------------------------------

@pytest.fixture
def trainings(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[m.__table__ for m in (
        Enterprise, Training, TrainingEnrolment, TrainingProgress, TrainingAssessmentSubmission,
        TrainingAssignmentSubmission, TrainingLessonAttendance, TrainingReview, TrainingOrder,
        TrainingLiveSession, TrainingWaitlist,
    )])
    sessions = sessionmaker(bind=engine)
    learner = {"id": str(uuid4()), "role": "learner", "email": "learner@example.com"}
    tid = uuid4()
    img = OLD_IP + UPLOAD_PATH
    with sessions() as db:
        db.add(Training(
            id=tid, enterprise_id=uuid4(), tenant_id=uuid4(), title="Live demo", category="Wellness",
            status="published", delivery_mode="online", price="0",
            primary_image=img, gallery_images=[img, OLD_IP + "/api/v1/trainings/upload/g2.png"],
            promotional_video=OLD_IP + "/api/v1/trainings/upload/promo.mp4", instructor_photo=img,
            notes_pdf_url=OLD_IP + "/api/v1/trainings/upload/notes.pdf",
            documents=[{"url": OLD_IP + "/api/v1/trainings/upload/brochure.pdf", "name": "Brochure"}],
            notes_documents=[{"title": "Week 1", "url": OLD_IP + "/api/v1/trainings/upload/w1.pdf"}],
            sections=[{"id": "s1", "type": "section", "title": "One", "lessons": [
                {"id": "l1", "type": "video", "title": "Intro", "is_mandatory": False,
                 "video_url": OLD_IP + "/api/v1/trainings/upload/intro.mp4",
                 "content_url": OLD_IP + "/api/v1/trainings/upload/intro.mp4",
                 "thumbnail_url": img,
                 "videos": [OLD_IP + "/api/v1/trainings/upload/extra.mp4"],
                 "documents": [{"url": OLD_IP + "/api/v1/trainings/upload/slides.pdf", "name": "Slides"}],
                 "notes": [OLD_IP + "/api/v1/trainings/upload/lesson-notes.pdf"]},
            ]}],
            assessments=[], assignments=[],
        ))
        db.add(TrainingEnrolment(training_id=tid, participant_name="Learner", participant_email=learner["email"], status="enrolled"))
        db.commit()

    app = FastAPI()
    app.add_middleware(HttpsLinkRewriteMiddleware)
    app.include_router(training_routes.router, prefix="/api/v1/trainings")

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: learner
    with TestClient(app) as client:
        yield client, tid
    engine.dispose()


@pytest.mark.parametrize("path", [
    "/api/v1/trainings/?status=published&page=1&page_size=50",
    "/api/v1/trainings/{tid}",
    "/api/v1/trainings/my/enrolments",
    "/api/v1/trainings/{tid}/content",
    "/api/v1/trainings/{tid}/progress",
])
def test_no_http_link_or_bare_ip_in_any_training_api_response(trainings, path):
    client, tid = trainings
    resp = client.get(path.format(tid=tid))
    assert resp.status_code == 200, resp.text
    assert "http://" not in resp.text and "13.207.85.164" not in resp.text


def test_live_demo_image_is_served_from_the_https_host(trainings):
    client, tid = trainings
    body = client.get(f"/api/v1/trainings/{tid}").json()
    assert body["primary_image"] == CANONICAL + UPLOAD_PATH
    assert body["gallery_images"][1] == f"{CANONICAL}/api/v1/trainings/upload/g2.png"
    assert body["promotional_video"] == f"{CANONICAL}/api/v1/trainings/upload/promo.mp4"
    assert body["instructor_photo"] == CANONICAL + UPLOAD_PATH
    assert body["notes_pdf_url"] == f"{CANONICAL}/api/v1/trainings/upload/notes.pdf"


def test_lesson_media_in_content_is_https(trainings):
    client, tid = trainings
    lesson = client.get(f"/api/v1/trainings/{tid}/content").json()["sections"][0]["lessons"][0]
    base = f"{CANONICAL}/api/v1/trainings/upload"
    assert lesson["video_url"] == f"{base}/intro.mp4" and lesson["content_url"] == f"{base}/intro.mp4"
    assert lesson["videos"] == [f"{base}/extra.mp4"]
    assert lesson["documents"][0]["url"] == f"{base}/slides.pdf"
    assert lesson["notes"] == [f"{base}/lesson-notes.pdf"]
    assert lesson["thumbnail_url"] == CANONICAL + UPLOAD_PATH


def test_my_enrolments_card_image_is_https(trainings):
    client, tid = trainings
    [row] = client.get("/api/v1/trainings/my/enrolments").json()
    assert row["primary_image"] == CANONICAL + UPLOAD_PATH


# --- write time ----------------------------------------------------------------------------

def test_new_uploads_return_an_https_url_even_if_the_base_is_still_http(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "http://chat.wisdomtooth.tech")
    monkeypatch.setattr(training_upload_service, "training_upload_root", lambda: tmp_path)
    out = training_upload_service.save_training_upload(b"\x89PNG\r\n\x1a\n" + b"0" * 20, "live-demo.png", "image/png", "image")
    assert out["url"].startswith(CANONICAL + "/api/v1/trainings/upload/")
    assert out["url"].endswith("_live-demo.png")


def test_saving_a_training_stores_https_links_only():
    payload = {
        "primary_image": OLD_IP + UPLOAD_PATH,
        "gallery_images": [OLD_IP + UPLOAD_PATH],
        "notes_pdf_url": "http://cdn.example.com/notes.pdf",
        "sections": [{"title": "S", "lessons": [{"title": "L", "type": "video", "video_url": OLD_IP + "/api/v1/trainings/upload/v.mp4"}]}],
    }
    out = normalize_authoring(payload)
    assert out["primary_image"] == CANONICAL + UPLOAD_PATH
    assert out["gallery_images"] == [CANONICAL + UPLOAD_PATH]
    assert out["notes_pdf_url"] == "https://cdn.example.com/notes.pdf"
    assert out["sections"][0]["lessons"][0]["video_url"] == CANONICAL + "/api/v1/trainings/upload/v.mp4"
    assert "http://" not in str(out)


def test_a_link_stored_before_https_only_still_passes_configured_media_validation(tmp_path, monkeypatch):
    """Clients echo back the image URL they were given; an old http://<ip> one must still match our upload."""
    from app.services import training_form_media

    stored = tmp_path / "b7c6fa47-1111-2222-3333-444455556666_live-demo.png"
    stored.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 20)
    monkeypatch.setattr("app.services.training_upload_service.training_upload_root", lambda: tmp_path)
    monkeypatch.setattr(training_form_media, "policy_cap", lambda field: 10_000)
    monkeypatch.setattr(training_form_media, "validate_media_bytes", lambda *a, **k: None)
    monkeypatch.setattr("app.services.training_form_rules.settings", lambda field: {"upload": {"max_size_bytes": 10_000}})
    monkeypatch.setattr("app.services.training_form_rules.empty", lambda v: not v)

    training_form_media.validate_media_value({"stub": True}, OLD_IP + UPLOAD_PATH)  # must not raise
