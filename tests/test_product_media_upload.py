"""Product media upload: POST /products/upload, the public file route (with Range for video), validation, access, and orphan clean-up.
Real routes, real files in a temporary upload directory."""
import os
import time
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import product as product_endpoints
from app.core.catalog_access import CatalogAccess, get_catalog_access
from app.core.config import settings
from app.db.database import Base, get_db
from app.models.enterprise_model import Enterprise
from app.models.product_model import Product
from app.services import product_media_service as media

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 60
GIF = b"GIF89a" + b"\x00" * 60
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 40
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 1000
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 200
MOV = b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 200


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "https://api.example.test")
    monkeypatch.setattr(settings, "PUBLIC_MEDIA_BASE_URL", "")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[Enterprise.__table__, Product.__table__])
    db = sessionmaker(bind=engine)()
    state = {"role": "admin"}
    app = FastAPI()
    app.include_router(product_endpoints.router, prefix="/api/v1/products")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_catalog_access] = lambda: CatalogAccess(state["role"], uuid4() if state["role"] not in ("public", "customer", "super_admin") else None)
    client = TestClient(app, raise_server_exceptions=False)

    def upload(data, name="photo.jpg", mime="image/jpeg"):
        return client.post("/api/v1/products/upload", files={"file": (name, data, mime)})

    yield type("Env", (), {"client": client, "state": state, "upload": staticmethod(upload), "db": db, "root": tmp_path / "products", "monkeypatch": monkeypatch})
    db.close()
    engine.dispose()


def stored_name(body):
    return body["url"].rsplit("/", 1)[1]


def test_a_photo_is_stored_and_the_answer_has_an_absolute_url(env):
    resp = env.upload(JPEG, "Yoga Mat.JPG")

    body = resp.json()
    assert resp.status_code == 201, resp.text
    assert set(body) == {"url", "name", "size", "type", "kind", "stored_name"}
    assert body["name"] == "Yoga Mat.JPG" and body["size"] == len(JPEG) and body["type"] == "image/jpeg" and body["kind"] == "image"
    assert body["url"] == f"https://api.example.test/api/v1/products/upload/{body['stored_name']}" and body["stored_name"].endswith(".jpg")
    assert (env.root / body["stored_name"]).read_bytes() == JPEG


def test_every_supported_type_is_accepted(env):
    cases = [(JPEG, "a.jpeg", "image/jpeg", "image"), (PNG, "a.png", "image/png", "image"), (WEBP, "a.webp", "image/webp", "image"), (GIF, "a.gif", "image/gif", "image"),
             (MP4, "a.mp4", "video/mp4", "video"), (WEBM, "a.webm", "video/webm", "video"), (MOV, "a.mov", "video/quicktime", "video")]
    for data, name, mime, kind in cases:
        body = env.upload(data, name, mime).json()
        assert body["kind"] == kind and body["type"] == mime, name


def test_without_a_public_base_the_url_uses_the_address_of_the_request(env):
    env.monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "")

    body = env.upload(PNG, "a.png", "image/png").json()

    assert body["url"].startswith("http://testserver/api/v1/products/upload/") and body["url"].endswith(".png")


def test_the_file_is_served_publicly_with_caching_and_the_right_type(env):
    body = env.upload(PNG, "a.png", "image/png").json()
    env.state["role"] = "public"                                                         # no sign-in needed to view

    resp = env.client.get(f"/api/v1/products/upload/{body['stored_name']}")

    assert resp.status_code == 200 and resp.content == PNG and resp.headers["content-type"] == "image/png"
    assert "immutable" in resp.headers["cache-control"] and resp.headers["x-content-type-options"] == "nosniff" and resp.headers["content-disposition"] == "inline"


def test_a_video_supports_range_requests_so_it_can_play_and_seek(env):
    body = env.upload(MP4, "clip.mp4", "video/mp4").json()
    url = f"/api/v1/products/upload/{body['stored_name']}"

    part = env.client.get(url, headers={"Range": "bytes=0-9"})
    tail = env.client.get(url, headers={"Range": "bytes=1000-"})

    assert part.status_code == 206 and part.content == MP4[:10] and part.headers["content-range"] == f"bytes 0-9/{len(MP4)}"
    assert tail.status_code == 206 and tail.content == MP4[1000:]
    assert env.client.get(url).headers["accept-ranges"] == "bytes" and env.client.get(url).headers["content-type"] == "video/mp4"


def test_a_browser_that_sends_no_type_is_accepted_when_the_extension_and_contents_agree(env):
    assert env.upload(PNG, "a.png", "application/octet-stream").json()["type"] == "image/png"
    assert env.upload(MP4, "a.mp4", "").json()["kind"] == "video"


def test_wrong_files_are_refused(env):
    mismatch = env.upload(JPEG, "a.png", "image/jpeg")
    assert mismatch.status_code == 415 and "does not match" in mismatch.json()["detail"]
    disguised = env.upload(b"<script>alert(1)</script>" * 5, "a.jpg", "image/jpeg")
    assert disguised.status_code == 415 and "not a real image/jpeg" in disguised.json()["detail"]
    assert env.upload(b"<svg xmlns='http://www.w3.org/2000/svg'/>", "a.svg", "image/svg+xml").status_code == 415
    assert env.upload(b"%PDF-1.4 " + b"0" * 40, "a.pdf", "application/pdf").status_code == 415
    assert env.upload(b"x" * 50, "a.exe", "application/octet-stream").status_code == 415
    empty = env.upload(b"", "a.jpg", "image/jpeg")
    assert empty.status_code == 400 and empty.json()["detail"] == "Uploaded file is empty"
    assert env.client.post("/api/v1/products/upload").status_code == 422
    assert list(env.root.iterdir()) == []                                                 # nothing refused is left on disk


def test_size_limits_are_per_kind(env):
    env.monkeypatch.setattr(settings, "PRODUCT_MAX_IMAGE_MB", 1)
    env.monkeypatch.setattr(settings, "PRODUCT_MAX_VIDEO_MB", 2)
    big_image = env.upload(JPEG + b"\x00" * (1024 * 1024), "big.jpg", "image/jpeg")
    ok_video = env.upload(MP4 + b"\x00" * (1024 * 1024), "ok.mp4", "video/mp4")                # 1 MB: fine for a video, too big for a photo
    big_video = env.upload(MP4 + b"\x00" * (2 * 1024 * 1024), "big.mp4", "video/mp4")

    assert big_image.status_code == 413 and "at most 1 MB" in big_image.json()["detail"]
    assert ok_video.status_code == 201 and big_video.status_code == 413 and "at most 2 MB" in big_video.json()["detail"]
    assert len(list(env.root.iterdir())) == 1                                                 # only the accepted video is stored


def test_the_default_limits_are_ten_mb_for_photos_and_fifty_for_videos():
    assert media.limits()["image"]["max_size_bytes"] == 10 * 1024 * 1024 and media.limits()["video"]["max_size_bytes"] == 50 * 1024 * 1024
    assert "image/svg+xml" not in media.limits()["image"]["mime_types"]


def test_only_enterprise_staff_can_upload_but_anyone_can_read_the_policy(env):
    for role, code in (("public", 401), ("customer", 403), ("provider", 403)):
        env.state["role"] = role
        resp = env.upload(JPEG)
        assert resp.status_code in (code, 403, 401), role
        assert resp.status_code != 201, role
    for role in ("admin", "super_admin"):
        env.state["role"] = role
        assert env.upload(JPEG).status_code == 201, role
    env.state["role"] = "public"
    policy = env.client.get("/api/v1/products/upload/policy").json()
    assert policy["image"]["max_size_bytes"] == 10 * 1024 * 1024 and "mp4" in policy["video"]["extensions"]


def test_only_names_an_upload_produced_can_be_fetched(env):
    for name in ("abc.jpg", "A" * 32 + ".jpg", "0" * 32 + ".svg", "0" * 32 + ".jpg", "0" * 32 + ".php"):
        assert env.client.get(f"/api/v1/products/upload/{name}").status_code == 404, name
    assert env.client.get("/api/v1/products/upload/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_old_uploads_no_product_uses_are_deleted_and_the_ones_in_use_are_kept(env):
    unused = env.upload(JPEG).json()["stored_name"]
    used = env.upload(PNG, "b.png", "image/png").json()["stored_name"]
    fresh = env.upload(GIF, "c.gif", "image/gif").json()["stored_name"]
    ent = Enterprise(tenant_id=uuid4(), business_short_name="S", business_legal_name="S Ltd", business_email="s@example.test", status="approved")
    env.db.add(ent)
    env.db.commit()
    env.db.add(Product(enterprise_id=ent.id, tenant_id=ent.tenant_id, product_name="Mat", product_category="Fitness", product_price=10.0,
                       product_images=f"https://api.example.test/api/v1/products/upload/{used}"))
    env.db.commit()
    old = time.time() - 8 * 86400
    for name in (unused, used):
        os.utime(env.root / name, (old, old))

    removed = media.purge_unreferenced(env.db)

    assert removed == 1 and not (env.root / unused).exists() and (env.root / used).exists() and (env.root / fresh).exists()
