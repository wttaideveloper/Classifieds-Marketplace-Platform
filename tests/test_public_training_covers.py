"""Training cover images load without Authorization (plain <Image> URL on mobile).

Public only for image files that a *published* training uses as primary_image or in gallery_images.
Everything else under /trainings/upload/ still needs a login.
"""
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import training as training_routes
from app.core.dependencies import get_optional_current_user
from app.db.database import Base, get_db
from app.models.training_model import Training
from app.services import training_upload_service

PNG = b"\x89PNG\r\n\x1a\n" + b"cover-bytes"
BASE = "https://chat.wisdomtooth.tech/api/v1/trainings/upload"


def stored(suffix: str) -> str:
    return f"{uuid4()}_{suffix}"


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    monkeypatch.setattr(training_upload_service, "training_upload_root", lambda: tmp_path)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[Training.__table__])
    sessions = sessionmaker(bind=engine)

    app = FastAPI()
    app.include_router(training_routes.router, prefix="/api/v1/trainings")

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    client = TestClient(app)  # no auth override: requests are anonymous unless a test logs in

    def add_file(name: str, data: bytes = PNG) -> str:
        (tmp_path / name).write_bytes(data)
        return name

    def add_training(**overrides):
        values = dict(
            enterprise_id=uuid4(), tenant_id=uuid4(), title="T", category="c",
            status="published", delivery_mode="online", gallery_images=[], sections=[],
        )
        values.update(overrides)
        with sessions() as db:
            db.add(Training(**values))
            db.commit()

    def login():
        # the endpoint resolves identity through get_optional_current_user (which calls get_current_user
        # as a plain function, so overriding get_current_user alone would not reach it)
        app.dependency_overrides[get_optional_current_user] = lambda: {"id": str(uuid4()), "role": "learner", "email": "l@example.com"}

    yield SimpleNamespace(client=client, add_file=add_file, add_training=add_training, login=login, app=app)
    engine.dispose()


from types import SimpleNamespace  # noqa: E402  (kept after the fixture for readability)


def get(world, name):
    return world.client.get(f"/api/v1/trainings/upload/{name}")


def test_primary_image_of_a_published_training_is_public(world):
    name = world.add_file(stored("cover.png"))
    world.add_training(primary_image=f"{BASE}/{name}")

    resp = get(world, name)
    assert resp.status_code == 200 and resp.content == PNG
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["cache-control"] == "public, max-age=86400"
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_gallery_images_of_a_published_training_are_public(world):
    first, second = world.add_file(stored("one.jpg")), world.add_file(stored("two.webp"))
    world.add_training(gallery_images=[f"{BASE}/{first}", f"{BASE}/{second}"])
    assert get(world, first).status_code == 200
    resp = get(world, second)
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/webp"


def test_old_http_ip_links_still_resolve_because_the_match_is_on_the_file_name(world):
    name = world.add_file(stored("legacy.png"))
    world.add_training(primary_image=f"http://13.207.85.164/api/v1/trainings/upload/{name}")
    assert get(world, name).status_code == 200


@pytest.mark.parametrize("overrides", [{"status": "draft"}, {"status": "pending_approval"}, {"status": "cancelled"}, {"is_deleted": True}])
def test_images_of_unpublished_or_deleted_trainings_stay_private(world, overrides):
    name = world.add_file(stored("cover.png"))
    world.add_training(primary_image=f"{BASE}/{name}", **overrides)
    assert get(world, name).status_code == 401


def test_an_upload_no_training_uses_stays_private(world):
    assert get(world, world.add_file(stored("orphan.png"))).status_code == 401


def test_non_image_files_are_never_public_even_on_a_published_training(world):
    pdf = world.add_file(stored("notes.pdf"), b"%PDF-1.4")
    video = world.add_file(stored("intro.mp4"), b"video")
    world.add_training(primary_image=f"{BASE}/{pdf}", gallery_images=[f"{BASE}/{video}"], notes_pdf_url=f"{BASE}/{pdf}")
    assert get(world, pdf).status_code == 401
    assert get(world, video).status_code == 401


def test_an_image_used_only_as_a_lesson_thumbnail_stays_private(world):
    name = world.add_file(stored("thumb.png"))
    world.add_training(sections=[{"id": "s", "lessons": [{"id": "l", "thumbnail_url": f"{BASE}/{name}"}]}])
    assert get(world, name).status_code == 401


def test_a_lookalike_file_name_does_not_inherit_publicity(world):
    """`_` is a LIKE wildcard — the match must be exact, not a pattern."""
    uid = uuid4()
    covered = world.add_file(f"{uid}_a_b.png")
    lookalike = world.add_file(f"{uid}_aXb.png")
    world.add_training(primary_image=f"{BASE}/{covered}")
    assert get(world, covered).status_code == 200
    assert get(world, lookalike).status_code == 401


def test_a_stale_or_invalid_token_is_just_anonymous(world):
    name = world.add_file(stored("cover.png"))
    secret = world.add_file(stored("draft.png"))
    world.add_training(primary_image=f"{BASE}/{name}")
    headers = {"Authorization": "Bearer not-a-real-token"}
    assert world.client.get(f"/api/v1/trainings/upload/{name}", headers=headers).status_code == 200
    assert world.client.get(f"/api/v1/trainings/upload/{secret}", headers=headers).status_code == 401


def test_logged_in_users_keep_access_to_every_upload_without_public_caching(world):
    private_image = world.add_file(stored("draft-cover.png"))
    pdf = world.add_file(stored("notes.pdf"), b"%PDF-1.4")
    world.add_training(status="draft", primary_image=f"{BASE}/{private_image}")
    world.login()
    for name in (private_image, pdf):
        resp = get(world, name)
        assert resp.status_code == 200
        assert "public" not in resp.headers.get("cache-control", "")  # never let a shared cache keep a private file


def test_bad_and_missing_names_behave_as_before(world):
    assert get(world, "not-a-valid-stored-name.png").status_code == 400
    assert get(world, f"{uuid4()}_missing.png").status_code == 404
