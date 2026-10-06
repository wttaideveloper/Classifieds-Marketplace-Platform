"""Event media uploads: primary image, gallery images, videos, documents.

Real SQLite tables and the real FastAPI app (via the Event test harness); files go to a temp dir.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.core.config import settings
from app.db.database import Base
from app.models.event_media_model import EventMedia
from app.models.event_model import Event
from app.services import event_media_service as media
from tests.event_sql_support import (
    API, client_for, customer_user, make_enterprise, make_event, make_session, reset_overrides,
    staff_user, super_admin_user,
)


def _no_network_side_effects(monkeypatch):
    """Same as the harness's silence_side_effects, minus the Celery patch — nothing here queues a task,
    and that helper needs `celery` installed just to import the task module."""
    monkeypatch.setattr("app.services.notification_triggers._safe_notify", lambda *a, **k: None)
    for target in (
        "app.services.super_admin_identity.fetch_internal_user_by_id",
        "app.services.super_admin_identity.fetch_auth_me_profile",
        "app.services.invigorate_auth_client.fetch_tenant_me_profile",
        "app.services.invigorate_auth_client.fetch_auth_me_profile",
    ):
        monkeypatch.setattr(target, lambda *a, **k: None)

BASE = "https://chat.wisdomtooth.tech"
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 200
JPEG = b"\xff\xd8\xff\xe0" + b"x" * 200
GIF = b"GIF89a" + b"x" * 200
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"x" * 200
WEBM = b"\x1a\x45\xdf\xa3" + b"x" * 200
PDF = b"%PDF-1.4\n" + b"x" * 200
DOCX = b"PK\x03\x04" + b"x" * 200
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture
def world(monkeypatch, tmp_path):
    _no_network_side_effects(monkeypatch)
    monkeypatch.setattr(settings, "FORCE_HTTPS_MEDIA_URLS", True)
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", BASE)
    monkeypatch.setattr(settings, "PUBLIC_MEDIA_BASE_URL", "")
    monkeypatch.setattr(media, "media_root", lambda: tmp_path)

    db = make_session()
    Base.metadata.create_all(db.bind, tables=[EventMedia.__table__])
    t1, t2 = uuid4(), uuid4()
    e1, e2 = make_enterprise(db, t1, "Acme"), make_enterprise(db, t2, "Other")
    monkeypatch.setattr(
        "app.services.event_form_config_service.apply_form_configuration_to_event_data",
        lambda _db, data, user: {"tenant_id": t1, "enterprise_id": e1.id, "form_configuration_id": None,
                                 "form_configuration_version_id": None, "custom_values": []},
    )
    owner, other_owner = staff_user(t1, "admin"), staff_user(t2, "admin")
    yield SimpleNamespace(
        db=db, t1=t1, t2=t2, e1=e1, e2=e2, owner=owner, other_owner=other_owner, root=tmp_path,
    )
    reset_overrides()
    db.close()


def as_user(w, user):
    """NB client_for sets app-wide auth overrides, so build the client right where it is used."""
    return client_for(w.db, user)


act = as_user


def upload(client, field, name, content_type, data, **form):
    return client.post(f"{API}/media/", files={"file": (name, data, content_type)}, data={"field": field, **form})


def put_asset(w, field="gallery_images", name="a.png", content_type="image/png", data=PNG, client=None):
    resp = upload(client or act(w, w.owner), field, name, content_type, data)
    assert resp.status_code == 201, resp.text
    return resp.json()


def draft_event(w, **overrides):
    return make_event(w.db, w.t1, enterprise=w.e1, status="draft", **overrides)


def reload(w, event):
    w.db.expire_all()
    return w.db.get(Event, event.id)


# --- policy ---------------------------------------------------------------------------------

def test_policy_lists_limits_per_field(world):
    body = as_user(world, None).get(f"{API}/media/policy").json()
    assert set(body) == {"primary_image", "gallery_images", "videos", "documents"}
    assert body["primary_image"]["max_count"] == 1 and body["gallery_images"]["max_count"] == 10
    assert body["videos"]["max_count"] == 3 and body["documents"]["max_count"] == 10
    assert body["videos"]["max_size_bytes"] == 100 * 1024 * 1024
    assert "image/gif" in body["gallery_images"]["mime_types"] and "image/gif" not in body["primary_image"]["mime_types"]
    assert "application/pdf" in body["documents"]["mime_types"] and "pdf" in body["documents"]["extensions"]


# --- upload ---------------------------------------------------------------------------------

@pytest.mark.parametrize(("field", "name", "mime", "data"), [
    ("primary_image", "cover.png", "image/png", PNG),
    ("primary_image", "cover.jpg", "image/jpeg", JPEG),
    ("gallery_images", "anim.gif", "image/gif", GIF),
    ("videos", "promo.mp4", "video/mp4", MP4),
    ("videos", "promo.webm", "video/webm", WEBM),
    ("documents", "Agenda.pdf", "application/pdf", PDF),
    ("documents", "Brief.docx", DOCX_MIME, DOCX),
])
def test_valid_file_is_stored_and_returned_as_a_hosted_https_asset(world, field, name, mime, data):
    asset = upload(act(world, world.owner), field, name, mime, data)
    assert asset.status_code == 201, asset.text
    body = asset.json()
    ext = name.rsplit(".", 1)[1]
    assert body["url"] == f"{BASE}/api/v1/events/media/{body['id']}.{ext}"
    assert body["field"] == field and body["name"] == name and body["size"] == len(data) and body["type"] == mime
    assert body["attached"] is False and body["expires_at"]
    assert (world.root / f"{body['id']}.{ext}").read_bytes() == data
    row = world.db.get(EventMedia, UUID(body["id"]))
    assert row.tenant_id == world.t1 and row.uploaded_by == world.owner["id"]


@pytest.mark.parametrize(("field", "name", "mime", "data", "code"), [
    ("documents", "promo.mp4", "video/mp4", MP4, 415),           # a video is not a document
    ("primary_image", "cover.gif", "image/gif", GIF, 415),       # gif is gallery-only
    ("gallery_images", "photo.png", "application/pdf", PNG, 415),  # declared type not allowed for the field
    ("gallery_images", "photo.jpg", "image/png", PNG, 415),      # extension does not match the type
    ("gallery_images", "fake.png", "image/png", b"not really a png at all", 415),  # contents do not match
    ("documents", "empty.pdf", "application/pdf", b"", 400),
])
def test_wrong_type_extension_or_contents_is_refused(world, field, name, mime, data, code):
    resp = upload(act(world, world.owner), field, name, mime, data)
    assert resp.status_code == code, resp.text
    assert list(world.root.iterdir()) == [] and world.db.query(EventMedia).count() == 0  # nothing left behind


def test_oversize_file_is_refused_and_removed(world, monkeypatch):
    monkeypatch.setitem(media.POLICY["gallery_images"], "max_bytes", 100)
    resp = upload(act(world, world.owner), "gallery_images", "big.png", "image/png", PNG)
    assert resp.status_code == 413 and "at most" in resp.json()["detail"]
    assert list(world.root.iterdir()) == []


def test_unknown_field_is_422(world):
    assert upload(act(world, world.owner), "banner", "a.png", "image/png", PNG).status_code == 422


def test_upload_requires_a_staff_login(world):
    assert upload(as_user(world, None), "gallery_images", "a.png", "image/png", PNG).status_code == 401
    assert upload(as_user(world, customer_user("c@example.com")), "gallery_images", "a.png", "image/png", PNG).status_code == 403


def test_admin_cannot_upload_against_another_tenants_enterprise(world):
    resp = upload(act(world, world.owner), "gallery_images", "a.png", "image/png", PNG, enterprise_id=str(world.e2.id))
    assert resp.status_code == 403
    assert upload(act(world, world.owner), "gallery_images", "a.png", "image/png", PNG, enterprise_id=str(world.e1.id)).status_code == 201


def test_platform_super_admin_uploads_for_a_named_enterprise(world):
    root = as_user(world, super_admin_user())
    assert upload(root, "gallery_images", "a.png", "image/png", PNG).status_code == 422  # must say which enterprise
    resp = upload(root, "gallery_images", "a.png", "image/png", PNG, enterprise_id=str(world.e2.id))
    assert resp.status_code == 201
    assert world.db.get(EventMedia, UUID(resp.json()["id"])).tenant_id == world.t2


def test_retrying_with_the_same_client_ref_returns_the_original_asset(world):
    first = upload(act(world, world.owner), "documents", "a.pdf", "application/pdf", PDF, client_ref="try-1")
    again = upload(act(world, world.owner), "documents", "a.pdf", "application/pdf", PDF, client_ref="try-1")
    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert world.db.query(EventMedia).count() == 1 and len(list(world.root.iterdir())) == 1
    other = upload(act(world, world.owner), "documents", "a.pdf", "application/pdf", PDF, client_ref="try-2")
    assert other.status_code == 201 and other.json()["id"] != first.json()["id"]


# --- saving an Event with uploaded files ----------------------------------------------------

def test_event_stores_the_uploaded_urls_and_expands_documents(world):
    cover = put_asset(world, "primary_image", "cover.png")
    gallery = put_asset(world, "gallery_images", "g.jpg", "image/jpeg", JPEG)
    video = put_asset(world, "videos", "v.mp4", "video/mp4", MP4)
    doc = put_asset(world, "documents", "Agenda.pdf", "application/pdf", PDF)
    event = draft_event(world)

    resp = act(world, world.owner).put(f"{API}/{event.id}", json={
        "primary_image": cover["url"], "gallery_images": [gallery["url"]], "videos": [video["url"]],
        # a client may send the whole asset object; name/size/type are re-read from our own record
        "documents": [{"id": doc["id"], "url": doc["url"], "name": "HACKED.exe", "size": 1, "type": "text/x-evil"}],
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["primary_image"] == cover["url"]
    assert body["gallery_images"] == [gallery["url"]] and body["videos"] == [video["url"]]
    assert body["documents"] == [{"id": doc["id"], "url": doc["url"], "name": "Agenda.pdf", "size": len(PDF), "type": "application/pdf"}]

    world.db.expire_all()
    assert all(world.db.get(EventMedia, UUID(a["id"])).attached_at for a in (cover, gallery, video, doc))
    assert upload(act(world, world.owner), "gallery_images", "z.png", "image/png", PNG).json()["attached"] is False


def test_create_accepts_uploaded_files_too(world):
    cover = put_asset(world, "primary_image", "cover.png")
    doc = put_asset(world, "documents", "Agenda.pdf", "application/pdf", PDF)
    start = datetime.utcnow() + timedelta(days=5)
    resp = act(world, world.owner).post(f"{API}/", json={
        "title": "Summit", "category": "Wellness", "status": "draft", "start_date": start.isoformat(),
        "end_date": (start + timedelta(hours=3)).isoformat(),
        "primary_image": cover["url"], "documents": [doc["url"]],
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["primary_image"] == cover["url"] and body["documents"][0]["name"] == "Agenda.pdf"
    world.db.expire_all()
    assert world.db.get(EventMedia, UUID(cover["id"])).attached_at is not None


def test_another_tenants_file_is_refused_without_revealing_it_exists(world):
    theirs = put_asset(world, client=as_user(world, world.other_owner))
    event = draft_event(world)
    resp = act(world, world.owner).put(f"{API}/{event.id}", json={"gallery_images": [theirs["url"]]})
    assert resp.status_code == 422
    assert resp.json()["detail"]["errors"] == [f"gallery_images: unknown or unauthorized uploaded file {theirs['id']}"]
    missing = f"{BASE}/api/v1/events/media/{uuid4()}.png"
    again = act(world, world.owner).put(f"{API}/{event.id}", json={"gallery_images": [missing]})
    assert again.status_code == 422 and "unknown or unauthorized" in again.json()["detail"]["errors"][0]
    assert reload(world, event).gallery_images in (None, [])


def test_a_file_cannot_be_used_in_a_field_that_does_not_accept_its_type(world):
    video = put_asset(world, "videos", "v.mp4", "video/mp4", MP4)
    event = draft_event(world)
    resp = act(world, world.owner).put(f"{API}/{event.id}", json={"documents": [video["url"]]})
    assert resp.status_code == 422 and "does not accept" in resp.json()["detail"]["errors"][0]


@pytest.mark.parametrize(("field", "limit"), [("gallery_images", 10), ("videos", 3), ("documents", 10)])
def test_item_count_limits(world, field, limit):
    event = draft_event(world)
    urls = [f"https://cdn.example.com/{i}.bin" for i in range(limit + 1)]
    resp = act(world, world.owner).put(f"{API}/{event.id}", json={field: urls})
    assert resp.status_code == 422 and f"at most {limit}" in resp.json()["detail"]["errors"][0]
    assert act(world, world.owner).put(f"{API}/{event.id}", json={field: urls[:limit]}).status_code == 200


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "data:image/png;base64,AAAA", "ftp://x/y.png", "/etc/passwd", "relative/path.png"])
def test_only_uploaded_files_or_http_urls_are_accepted(world, bad):
    event = draft_event(world)
    resp = act(world, world.owner).put(f"{API}/{event.id}", json={"primary_image": bad})
    assert resp.status_code == 422 and "https://" in resp.json()["detail"]["errors"][0]


def test_external_urls_still_work_and_are_stored_as_https(world):
    event = draft_event(world)
    resp = act(world, world.owner).put(f"{API}/{event.id}", json={
        "primary_image": "http://images.pexels.com/p.jpg", "gallery_images": ["https://cdn.example.com/g.png"],
        "documents": [{"url": "http://files.example.com/brochure.pdf", "name": "Brochure", "type": "application/pdf", "extra": "dropped"}],
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["primary_image"] == "https://images.pexels.com/p.jpg"
    assert body["documents"] == [{"url": "https://files.example.com/brochure.pdf", "name": "Brochure", "type": "application/pdf"}]


def test_omitted_fields_are_unchanged_and_null_or_empty_clears(world):
    cover = put_asset(world, "primary_image", "cover.png")
    event = draft_event(world)
    act(world, world.owner).put(f"{API}/{event.id}", json={"primary_image": cover["url"], "gallery_images": ["https://cdn.example.com/g.png"]})

    assert act(world, world.owner).put(f"{API}/{event.id}", json={"title": "Renamed"}).status_code == 200
    kept = reload(world, event)
    assert kept.primary_image == cover["url"] and kept.gallery_images == ["https://cdn.example.com/g.png"]

    assert act(world, world.owner).put(f"{API}/{event.id}", json={"primary_image": None, "gallery_images": []}).status_code == 200
    cleared = reload(world, event)
    assert cleared.primary_image is None and cleared.gallery_images == []


# --- serving files --------------------------------------------------------------------------

def _asset_event(w, status, field="gallery_images", name="a.png", mime="image/png", data=PNG, kind="gallery_images"):
    asset = put_asset(w, field, name, mime, data)
    stored = asset["url"] if kind != "documents" else {"id": asset["id"], "url": asset["url"], "name": name, "size": len(data), "type": mime}
    value = {"primary_image": asset["url"]} if kind == "primary_image" else {kind: [stored]}
    event = make_event(w.db, w.t1, enterprise=w.e1, status=status, **value)
    return asset, event


def path_of(asset):
    return asset["url"].replace(BASE, "")


def test_cover_images_of_published_events_are_public_and_cacheable(world):
    asset, _ = _asset_event(world, "published", kind="gallery_images")
    resp = as_user(world, None).get(path_of(asset))
    assert resp.status_code == 200 and resp.content == PNG
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["cache-control"] == "public, max-age=86400" and resp.headers["x-content-type-options"] == "nosniff"

    cover, _ = _asset_event(world, "published", field="primary_image", kind="primary_image")
    assert as_user(world, None).get(path_of(cover)).status_code == 200


def test_drafts_and_unattached_uploads_are_visible_only_to_the_owning_tenant_and_super_admin(world):
    draft, _ = _asset_event(world, "draft")
    pending = put_asset(world)
    for asset in (draft, pending):
        url = path_of(asset)
        assert as_user(world, None).get(url).status_code == 401
        assert as_user(world, customer_user("c@example.com")).get(url).status_code == 403
        assert as_user(world, world.other_owner).get(url).status_code == 403   # another tenant
        assert act(world, world.owner).get(url).status_code == 200
        assert as_user(world, super_admin_user()).get(url).status_code == 200  # approval review


def test_videos_and_documents_of_a_published_event_need_a_login_but_not_ownership(world):
    video, _ = _asset_event(world, "published", "videos", "v.mp4", "video/mp4", MP4, "videos")
    doc, _ = _asset_event(world, "published", "documents", "Agenda 2026.pdf", "application/pdf", PDF, "documents")
    attendee = customer_user("a@example.com")
    for asset in (video, doc):
        assert as_user(world, None).get(path_of(asset)).status_code == 401
        assert as_user(world, attendee).get(path_of(asset)).status_code == 200
    download = as_user(world, attendee).get(path_of(doc))
    disposition = download.headers["content-disposition"]
    assert disposition.startswith("attachment") and "Agenda%202026.pdf" in disposition  # RFC 5987 filename*
    assert "cache-control" not in download.headers or "public" not in download.headers["cache-control"]
    assert "attachment" not in as_user(world, attendee).get(path_of(video)).headers.get("content-disposition", "")  # video plays inline


def test_unknown_or_malformed_file_names_are_404(world):
    asset = put_asset(world)
    client = as_user(world, super_admin_user())
    assert client.get(f"{API}/media/{uuid4()}.png").status_code == 404
    assert client.get(f"{API}/media/{asset['id']}.jpg").status_code == 404   # wrong extension
    assert client.get(f"{API}/media/not-an-id.png").status_code == 404
    assert client.get(f"{API}/media/{asset['id']}").status_code == 404       # extension is part of the name


# --- removing, replacing, abandoning --------------------------------------------------------

def test_discarding_an_unused_upload(world):
    asset = put_asset(world)
    assert as_user(world, world.other_owner).delete(f"{API}/media/{asset['id']}").status_code == 404
    assert act(world, world.owner).delete(f"{API}/media/{asset['id']}").json() == {"deleted": True}
    assert world.db.get(EventMedia, UUID(asset["id"])) is None and list(world.root.iterdir()) == []


def test_a_file_an_event_uses_cannot_be_discarded(world):
    asset = put_asset(world)
    event = draft_event(world)
    act(world, world.owner).put(f"{API}/{event.id}", json={"gallery_images": [asset["url"]]})
    resp = act(world, world.owner).delete(f"{API}/media/{asset['id']}")
    assert resp.status_code == 409 and "remove it from the Event" in resp.json()["detail"]


def test_replacing_a_file_releases_the_old_one_then_deletes_it_after_the_grace_period(world):
    old, new = put_asset(world, name="old.png"), put_asset(world, name="new.png")
    event = draft_event(world)
    act(world, world.owner).put(f"{API}/{event.id}", json={"gallery_images": [old["url"]]})
    act(world, world.owner).put(f"{API}/{event.id}", json={"gallery_images": [new["url"]]})  # replace

    world.db.expire_all()
    old_row = world.db.get(EventMedia, UUID(old["id"]))
    assert old_row.released_at is not None and world.db.get(EventMedia, UUID(new["id"])).released_at is None

    assert media.purge_unreferenced_event_media(world.db, now=datetime.utcnow() + timedelta(hours=23)) == 0  # still in grace
    assert (world.root / f"{old['id']}.png").exists()
    assert media.purge_unreferenced_event_media(world.db, now=datetime.utcnow() + timedelta(hours=25)) == 1
    assert not (world.root / f"{old['id']}.png").exists() and world.db.get(EventMedia, UUID(old["id"])) is None
    assert (world.root / f"{new['id']}.png").exists()  # the one in use is never touched


def test_abandoned_uploads_are_deleted_after_24_hours_but_used_ones_never(world):
    abandoned, used = put_asset(world, name="a.png"), put_asset(world, name="u.png")
    event = draft_event(world)
    act(world, world.owner).put(f"{API}/{event.id}", json={"gallery_images": [used["url"]]})

    assert media.purge_unreferenced_event_media(world.db, now=datetime.utcnow() + timedelta(hours=1)) == 0
    assert media.purge_unreferenced_event_media(world.db, now=datetime.utcnow() + timedelta(days=30)) == 1
    assert world.db.get(EventMedia, UUID(abandoned["id"])) is None
    assert world.db.get(EventMedia, UUID(used["id"])) is not None


def test_a_file_still_used_by_a_duplicate_event_survives_its_removal_from_the_original(world):
    asset = put_asset(world)
    original = draft_event(world)
    act(world, world.owner).put(f"{API}/{original.id}", json={"gallery_images": [asset["url"]]})
    assert act(world, world.owner).post(f"{API}/{original.id}/duplicate").status_code == 201

    act(world, world.owner).put(f"{API}/{original.id}", json={"gallery_images": []})
    assert media.purge_unreferenced_event_media(world.db, now=datetime.utcnow() + timedelta(days=30)) == 0
    assert (world.root / f"{asset['id']}.png").exists()


def test_upload_housekeeping_runs_opportunistically(world):
    stale = put_asset(world, name="stale.png")
    row = world.db.get(EventMedia, UUID(stale["id"]))
    row.created_at = datetime.utcnow() - timedelta(days=3)
    world.db.commit()
    put_asset(world, name="fresh.png")  # any later upload sweeps expired leftovers
    world.db.expire_all()
    assert world.db.get(EventMedia, UUID(stale["id"])) is None


# --- link OR upload, mixed -------------------------------------------------------------------

def test_every_field_accepts_a_link_or_an_upload_and_lists_may_mix_them(world):
    up_cover = put_asset(world, "primary_image", "cover.png")
    up_g1 = put_asset(world, "gallery_images", "g1.png")
    up_g2 = put_asset(world, "gallery_images", "g2.jpg", "image/jpeg", JPEG)
    up_video = put_asset(world, "videos", "v.mp4", "video/mp4", MP4)
    up_doc = put_asset(world, "documents", "Agenda.pdf", "application/pdf", PDF)
    event = draft_event(world)

    resp = act(world, world.owner).put(f"{API}/{event.id}", json={
        "primary_image": up_cover["url"],
        "gallery_images": [up_g1["url"], "https://images.pexels.com/photos/1/p.jpeg?auto=compress", up_g2["url"]],
        "videos": ["https://www.youtube.com/watch?v=abc123", up_video["url"]],
        "documents": [
            up_doc["url"],
            "https://cdn.example.com/files/Annual%20Report.pdf?v=2",
            {"url": "https://s3.amazonaws.com/bucket/brochure.pdf", "name": "Brochure"},
        ],
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["primary_image"] == up_cover["url"]
    assert body["gallery_images"] == [up_g1["url"], "https://images.pexels.com/photos/1/p.jpeg?auto=compress", up_g2["url"]]  # order kept
    assert body["videos"] == ["https://www.youtube.com/watch?v=abc123", up_video["url"]]
    assert body["documents"] == [
        {"id": up_doc["id"], "url": up_doc["url"], "name": "Agenda.pdf", "size": len(PDF), "type": "application/pdf"},
        {"url": "https://cdn.example.com/files/Annual%20Report.pdf?v=2", "name": "Annual Report.pdf"},
        {"url": "https://s3.amazonaws.com/bucket/brochure.pdf", "name": "Brochure"},
    ]
    world.db.expire_all()
    assert all(world.db.get(EventMedia, UUID(a["id"])).attached_at for a in (up_cover, up_g1, up_g2, up_video, up_doc))


def test_the_count_limit_covers_links_and_uploads_together(world):
    uploads = [put_asset(world, name=f"g{i}.png") for i in range(6)]
    links = [f"https://cdn.example.com/{i}.png" for i in range(5)]
    event = draft_event(world)
    resp = act(world, world.owner).put(f"{API}/{event.id}", json={"gallery_images": [u["url"] for u in uploads] + links})
    assert resp.status_code == 422 and "at most 10 allowed, got 11" in resp.json()["detail"]["errors"][0]
    ok = act(world, world.owner).put(f"{API}/{event.id}", json={"gallery_images": [u["url"] for u in uploads] + links[:4]})
    assert ok.status_code == 200


def test_switching_between_a_link_and_an_upload(world):
    upload_asset = put_asset(world, "primary_image", "cover.png")
    event = draft_event(world)
    put = lambda value: act(world, world.owner).put(f"{API}/{event.id}", json={"primary_image": value})

    assert put("https://images.pexels.com/photos/1/p.jpeg").json()["primary_image"] == "https://images.pexels.com/photos/1/p.jpeg"
    assert put(upload_asset["url"]).json()["primary_image"] == upload_asset["url"]
    world.db.expire_all()
    assert world.db.get(EventMedia, UUID(upload_asset["id"])).attached_at is not None

    assert put("https://images.pexels.com/photos/2/q.jpeg").status_code == 200  # back to a link
    world.db.expire_all()
    assert world.db.get(EventMedia, UUID(upload_asset["id"])).released_at is not None  # the unused upload starts its grace period


def test_the_same_file_or_link_listed_twice_is_stored_once(world):
    asset = put_asset(world)
    event = draft_event(world)
    resp = act(world, world.owner).put(f"{API}/{event.id}", json={
        "gallery_images": [asset["url"], asset["url"], "https://cdn.example.com/a.png", "https://cdn.example.com/a.png"],
    })
    assert resp.json()["gallery_images"] == [asset["url"], "https://cdn.example.com/a.png"]


# --- link validation ------------------------------------------------------------------------

@pytest.mark.parametrize(("link", "reason"), [
    ("http://13.207.85.164/pic.png", "public hostname"),
    ("https://192.168.0.5/pic.png", "public hostname"),
    ("https://[::1]/pic.png", "public hostname"),
    ("https://localhost/pic.png", "public hostname"),
    ("https://intranet/pic.png", "public hostname"),          # single-label host
    ("https://user:secret@cdn.example.com/pic.png", "username or password"),
    ("https://cdn.example.com/my pic.png", "spaces"),
    ("https://cdn.example.com/" + "a" * 2100, "longer than"),
    ("javascript:alert(1)", "https:// URL"),
    ("data:image/png;base64,AAAA", "https:// URL"),
    ("//cdn.example.com/pic.png", "https:// URL"),
    ("", "needs a url"),
])
def test_links_that_are_not_ordinary_public_urls_are_refused(world, link, reason):
    event = draft_event(world)
    resp = act(world, world.owner).put(f"{API}/{event.id}", json={"gallery_images": [link]})
    assert resp.status_code == 422, resp.text
    assert reason in resp.json()["detail"]["errors"][0]


@pytest.mark.parametrize("link", [
    "https://www.youtube.com/watch?v=abc123",
    "https://youtu.be/abc123",
    "https://vimeo.com/123456",
    "https://bucket.s3.ap-south-1.amazonaws.com/events/pic.png?X-Amz-Signature=abc&X-Amz-Expires=3600",
    "https://images.pexels.com/photos/1/p.jpeg?auto=compress&cs=tinysrgb",
    "http://cdn.example.com/pic.png",
])
def test_ordinary_links_are_accepted_whatever_the_field(world, link):
    event = draft_event(world)
    for field in ("gallery_images", "videos", "documents"):
        assert act(world, world.owner).put(f"{API}/{event.id}", json={field: [link]}).status_code == 200


def test_a_document_link_without_a_name_gets_one_from_the_url(world):
    event = draft_event(world)
    resp = act(world, world.owner).put(f"{API}/{event.id}", json={"documents": [
        "https://cdn.example.com/files/Terms%20and%20Conditions.pdf", "https://docs.example.com",
    ]})
    assert [d["name"] for d in resp.json()["documents"]] == ["Terms and Conditions.pdf", "docs.example.com"]
