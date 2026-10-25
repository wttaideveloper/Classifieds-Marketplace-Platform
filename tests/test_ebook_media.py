"""Image assets for eBooks: what an upload checks, the virus scan, the asset record, who can fetch the file, and deleting."""
import socket
import struct
import threading
from uuid import UUID

import pytest

from app.core.config import settings
from app.models.ebook_model import MediaAsset
from app.services import media_service, virus_scan
from tests.ebook_support import decide, doc, go_live, image_bytes, image_block, page, saved, submit, upload, world  # noqa: F401


def post_upload(world, content, filename="photo.png", content_type="image/png", user=None, purpose="image"):
    return world.as_user(user or world.people.author).post("/trainings/upload", files={"file": (filename, content, content_type)}, data={"purpose": purpose})


@pytest.fixture(autouse=True)
def no_form_policy(monkeypatch):
    monkeypatch.setattr("app.services.training_form_media.resolve_upload_field", lambda *a, **k: None)


# --- what an upload checks and what comes back ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("fmt,name,mime", [("PNG", "a.png", "image/png"), ("JPEG", "a.jpg", "image/jpeg"), ("JPEG", "a.jpeg", "image/jpeg"), ("WEBP", "a.webp", "image/webp"), ("GIF", "a.gif", "image/gif")])
def test_pictures_are_accepted_and_recorded_with_their_size(world, fmt, name, mime):
    resp = post_upload(world, image_bytes(fmt, (640, 480)), name, mime)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["purpose"] == "image" and body["width"] == 640 and body["height"] == 480 and len(body["sha256"]) == 64 and body["scan_status"] == "skipped"
    assert body["asset_url"].endswith(f"/api/v1/media/{body['asset_id']}/file") and "/api/v1/trainings/upload/" in body["url"]
    asset = world.db.get(MediaAsset, UUID(body["asset_id"]))
    assert asset.content_type == mime.replace("image/jpg", "image/jpeg") and asset.size == body["size"] and str(asset.tenant_id) == world.people.author["tenant_id"]
    assert str(asset.owner_user_id) == world.people.author["id"] and (world.tmp / asset.stored_name).is_file()


def test_svg_is_not_accepted_because_it_can_carry_scripts(world):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(1)</script></svg>'

    resp = post_upload(world, svg, "logo.svg", "image/svg+xml")

    assert resp.status_code == 415 and world.db.query(MediaAsset).count() == 0


@pytest.mark.parametrize("content,name,mime", [
    (b"<html><script>alert(1)</script></html>", "photo.png", "image/png"),              # not a picture at all
    (b"not an image", "photo.jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 50, "truncated.png", "image/png"),                 # a PNG header with nothing behind it
])
def test_a_file_that_is_not_really_a_picture_is_refused(world, content, name, mime):
    assert post_upload(world, content, name, mime).status_code == 415
    assert world.db.query(MediaAsset).count() == 0 and list(world.tmp.iterdir()) == []                # and nothing was stored


def test_the_name_must_match_the_picture(world):
    resp = post_upload(world, image_bytes("PNG"), "photo.jpg", "image/jpeg")

    assert resp.status_code == 415 and "PNG" in resp.json()["detail"]


def test_oversize_pictures_are_refused(world, monkeypatch):
    monkeypatch.setattr(settings, "MAX_IMAGE_DIMENSION", 500)

    too_wide = post_upload(world, image_bytes("PNG", (800, 100)))
    assert too_wide.status_code == 422 and "500 pixels" in too_wide.json()["detail"]
    monkeypatch.setattr(settings, "MAX_IMAGE_DIMENSION", 8000)
    monkeypatch.setattr(settings, "MAX_IMAGE_PIXELS", 100_000)
    too_many = post_upload(world, image_bytes("PNG", (400, 400)))
    assert too_many.status_code == 422 and "too many pixels" in too_many.json()["detail"]
    assert world.db.query(MediaAsset).count() == 0


def test_the_file_size_limit_still_applies(world, monkeypatch):
    from app.services import training_upload_service as tus

    monkeypatch.setitem(tus.MAX_SIZE_BYTES, "image", 1000)

    assert post_upload(world, image_bytes("PNG", (800, 600))).status_code in (413,)


def test_other_kinds_of_upload_are_unchanged_and_have_no_asset(world):
    resp = post_upload(world, b"%PDF-1.4 minimal", "notes.pdf", "application/pdf", purpose="lesson_pdf")

    assert resp.status_code == 201 and resp.json()["asset_id"] is None and world.db.query(MediaAsset).count() == 0


def test_only_staff_upload(world):
    assert post_upload(world, image_bytes("PNG"), user=world.people.learner).status_code == 403
    assert world.as_user(None).post("/trainings/upload", files={"file": ("a.png", image_bytes("PNG"), "image/png")}).status_code == 401


# --- the virus scan --------------------------------------------------------------------------------------------------------------------------------------

class FakeClamd:
    """A tiny clamd: reads the INSTREAM chunks, then answers with `reply`."""

    def __init__(self, reply: bytes):
        self.reply, self.received = reply, b""
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn, _ = self.sock.accept()
        with conn:
            buffer = b""
            while True:
                part = conn.recv(65536)
                if not part:
                    return
                buffer += part
                if buffer.startswith(b"zINSTREAM\0") and buffer.endswith(struct.pack("!I", 0)):
                    break
            self.received = buffer
            conn.sendall(self.reply)

    def close(self):
        self.sock.close()


@pytest.mark.parametrize("reply,status", [(b"stream: OK\0", "clean"), (b"stream: Eicar-Test-Signature FOUND\0", "infected"), (b"stream: scan error ERROR\0", "error")])
def test_the_scanner_protocol(monkeypatch, reply, status):
    server = FakeClamd(reply)
    monkeypatch.setattr(settings, "CLAMAV_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "CLAMAV_PORT", server.port)
    data = b"x" * 150_000                                            # more than one chunk

    result = virus_scan.scan_bytes(data)

    assert result.status == status
    assert server.received.startswith(b"zINSTREAM\0")
    framed, rebuilt, chunks = server.received[len(b"zINSTREAM\0"):], b"", 0
    while True:                                                      # <4-byte length><bytes> ... then a zero length
        (size,) = struct.unpack("!I", framed[:4])
        if size == 0:
            break
        rebuilt, framed, chunks = rebuilt + framed[4:4 + size], framed[4 + size:], chunks + 1
    assert rebuilt == data and chunks == 3                          # 150,000 bytes in 64 KB chunks
    if status == "infected":
        assert result.signature == "Eicar-Test-Signature"
    server.close()


def test_an_infected_upload_is_blocked_and_nothing_is_stored(world, monkeypatch):
    server = FakeClamd(b"stream: Win.Test.EICAR FOUND\0")
    monkeypatch.setattr(settings, "CLAMAV_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "CLAMAV_PORT", server.port)

    resp = post_upload(world, image_bytes("PNG"))

    assert resp.status_code == 422 and "virus scan" in resp.json()["detail"]
    assert world.db.query(MediaAsset).count() == 0 and list(world.tmp.iterdir()) == []
    server.close()


def test_a_clean_scan_is_recorded(world, monkeypatch):
    server = FakeClamd(b"stream: OK\0")
    monkeypatch.setattr(settings, "CLAMAV_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "CLAMAV_PORT", server.port)

    resp = post_upload(world, image_bytes("PNG"))

    assert resp.status_code == 201 and resp.json()["scan_status"] == "clean"
    server.close()


def test_an_unreachable_scanner_either_blocks_or_marks_the_file(world, monkeypatch):
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    port = dead.getsockname()[1]
    dead.close()
    monkeypatch.setattr(settings, "CLAMAV_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "CLAMAV_PORT", port)

    soft = post_upload(world, image_bytes("PNG"))
    assert soft.status_code == 201 and soft.json()["scan_status"] == "unscanned"
    monkeypatch.setattr(settings, "CLAMAV_REQUIRED", True)
    hard = post_upload(world, image_bytes("PNG"))
    assert hard.status_code == 503 and "scanner is not available" in hard.json()["detail"]
    assert world.db.query(MediaAsset).count() == 1


# --- asset details ------------------------------------------------------------------------------------------------------------------------------------------

def test_the_business_sees_an_assets_details_and_where_it_is_used(world):
    asset = upload(world, size=(1000, 500))
    saved(world, doc([page("p1", "One", images=[image_block(asset)])]))

    info = world.as_user(world.people.author).get(f"/media/{asset}").json()

    assert info["asset_id"] == asset and info["width"] == 1000 and info["height"] == 500 and info["type"] == "image/png" and info["scan_status"] == "skipped"
    assert info["used_by"] == [{"training_id": str(world.training.id), "section_id": "sec-1", "lesson_id": "les-ebook", "in": "draft"}]
    assert world.as_user(world.people.root).get(f"/media/{asset}").status_code == 200
    assert world.as_user(world.people.provider).get(f"/media/{asset}").status_code == 200


def test_others_cannot_see_an_assets_details(world):
    asset = upload(world)

    for user in (world.people.outsider, world.people.learner, world.people.stranger):
        assert world.as_user(user).get(f"/media/{asset}").status_code == 404, user["name"]
    assert world.as_user(None).get(f"/media/{asset}").status_code == 401
    assert world.as_user(world.people.author).get("/media/not-an-id").status_code == 404
    assert world.as_user(world.people.author).get("/media/00000000-0000-4000-8000-000000000000").status_code == 404


# --- who can fetch the file ----------------------------------------------------------------------------------------------------------------------------------

def test_staff_and_super_admins_fetch_the_file(world):
    asset = upload(world)

    resp = world.as_user(world.people.author).get(f"/media/{asset}/file")

    assert resp.status_code == 200 and resp.headers["content-type"] == "image/png" and resp.content == (world.tmp / world.db.query(MediaAsset).one().stored_name).read_bytes()
    assert resp.headers["x-content-type-options"] == "nosniff" and resp.headers["cache-control"].startswith("private")
    assert world.as_user(world.people.root).get(f"/media/{asset}/file").status_code == 200
    assert world.as_user(world.people.provider).get(f"/media/{asset}/file").status_code == 200


def test_another_business_and_the_public_cannot_fetch_the_file(world):
    asset = upload(world)

    assert world.as_user(world.people.outsider).get(f"/media/{asset}/file").status_code == 404
    assert world.as_user(None).get(f"/media/{asset}/file").status_code == 401


def test_a_learner_fetches_only_pictures_of_a_published_ebook_they_are_enrolled_in(world):
    used, draft_only, unused = upload(world), upload(world), upload(world)
    go_live(world, doc([page("p1", "One", images=[image_block(used)])]))
    saved(world, doc([page("p1", "One", images=[image_block(used), image_block(draft_only, "i2")])]))              # added to the draft, not published

    learner, stranger = world.people.learner, world.people.stranger

    assert world.as_user(learner).get(f"/media/{used}/file").status_code == 200
    assert world.as_user(learner).get(f"/media/{draft_only}/file").status_code == 404
    assert world.as_user(learner).get(f"/media/{unused}/file").status_code == 404
    assert world.as_user(stranger).get(f"/media/{used}/file").status_code == 404                                    # not enrolled
    world.training.status = "draft"
    world.db.commit()
    assert world.as_user(learner).get(f"/media/{used}/file").status_code == 404                                     # the training is not open
    world.training.status = "published"
    world.db.commit()
    world.as_user(world.people.author).post(world.base + "/unpublish")
    assert world.as_user(learner).get(f"/media/{used}/file").status_code == 404                                     # taken down


def test_a_missing_file_is_a_404_not_an_error(world):
    asset = upload(world)
    (world.tmp / world.db.query(MediaAsset).one().stored_name).unlink()

    assert world.as_user(world.people.author).get(f"/media/{asset}/file").status_code == 404


# --- deleting and replacing ------------------------------------------------------------------------------------------------------------------------------------

def test_an_unused_picture_is_deleted_with_its_file(world):
    asset = upload(world)
    stored = world.db.query(MediaAsset).one().stored_name

    resp = world.as_user(world.people.author).delete(f"/media/{asset}")

    assert resp.status_code == 200 and not (world.tmp / stored).exists()
    assert world.as_user(world.people.author).get(f"/media/{asset}").status_code == 404
    assert world.as_user(world.people.author).delete(f"/media/{asset}").status_code == 404
    assert world.db.query(MediaAsset).one().is_deleted is True and world.db.query(MediaAsset).one().deleted_at


def test_a_picture_an_ebook_still_uses_cannot_be_deleted(world):
    asset = upload(world)
    saved(world, doc([page("p1", "One", images=[image_block(asset)])]))

    blocked = world.as_user(world.people.author).delete(f"/media/{asset}")

    assert blocked.status_code == 409
    detail = blocked.json()["detail"]
    assert detail["code"] == "ASSET_IN_USE" and detail["used_by"][0]["in"] == "draft" and detail["used_by"][0]["lesson_id"] == "les-ebook"
    assert world.db.query(MediaAsset).one().is_deleted is False


def test_a_picture_in_the_live_copy_stays_protected_after_it_leaves_the_draft(world):
    asset = upload(world)
    go_live(world, doc([page("p1", "One", images=[image_block(asset)])]))
    saved(world, doc([page("p1", "One")]))                                                                           # removed from the draft only

    blocked = world.as_user(world.people.author).delete(f"/media/{asset}")

    assert blocked.status_code == 409 and [u["in"] for u in blocked.json()["detail"]["used_by"]] == ["published"]


def test_replacing_a_picture_means_new_asset_then_delete_the_old_one(world):
    old, new = upload(world), upload(world, size=(1200, 700))
    saved(world, doc([page("p1", "One", images=[image_block(old)])]))
    assert world.as_user(world.people.author).delete(f"/media/{old}").status_code == 409

    saved(world, doc([page("p1", "One", images=[image_block(new)])]))

    assert world.as_user(world.people.author).delete(f"/media/{old}").status_code == 200
    assert world.as_user(world.people.author).delete(f"/media/{new}").status_code == 409


def test_only_the_business_deletes_its_pictures(world):
    asset = upload(world)

    for user in (world.people.outsider, world.people.learner):
        assert world.as_user(user).delete(f"/media/{asset}").status_code == 404
    assert world.as_user(world.people.root).delete(f"/media/{asset}").status_code == 200
    assert world.as_user(None).delete(f"/media/{upload(world)}").status_code == 401


def test_pictures_are_checked_when_the_ebook_is_validated_after_a_delete(world):
    asset = upload(world)
    saved(world, doc([page("p1", "One")]))
    world.as_user(world.people.author).delete(f"/media/{asset}")
    saved(world, doc([page("p1", "One", images=[image_block(asset)])]))

    report = world.as_user(world.people.author).post(world.base + "/validate", json={}).json()

    assert report["valid"] is False and report["errors"][0]["code"] == "asset_unavailable" and "does not exist" in report["errors"][0]["message"]


# --- pictures whose business is only known from the sign-in token, and pictures saved with no business ---------------------------------------

def _tenant_only_from_token(monkeypatch, tenant):
    monkeypatch.setattr("app.core.auth_context.resolve_auth_tenant_id_with_db",
                        lambda _db, user, access_token=None: user.get("tenant_id") or (tenant if access_token == "tok" else None))


def test_the_sign_in_token_is_used_to_find_the_business_of_an_upload(world, monkeypatch):
    _tenant_only_from_token(monkeypatch, world.people.author["tenant_id"])
    author = {**world.people.author, "tenant_id": None}

    resp = world.as_user(author).post("/trainings/upload", files={"file": ("a.png", image_bytes("PNG"), "image/png")}, data={"purpose": "image"},
                                      headers={"Authorization": "Bearer tok"})

    assert resp.status_code == 201, resp.text
    assert str(world.db.get(MediaAsset, UUID(resp.json()["asset_id"])).tenant_id) == world.people.author["tenant_id"]


def test_an_upload_is_refused_when_the_business_cannot_be_found(world, monkeypatch):
    _tenant_only_from_token(monkeypatch, world.people.author["tenant_id"])

    resp = world.as_user({**world.people.author, "tenant_id": None}).post("/trainings/upload", files={"file": ("a.png", image_bytes("PNG"), "image/png")}, data={"purpose": "image"})

    assert resp.status_code == 403 and world.db.query(MediaAsset).count() == 0 and list(world.tmp.iterdir()) == []


def _orphan(world):
    asset_id = upload(world)
    asset = world.db.get(MediaAsset, UUID(asset_id))
    asset.tenant_id = None
    world.db.commit()
    return asset_id


def test_an_old_picture_without_a_business_can_still_be_opened_by_its_uploader_and_is_repaired(world):
    asset_id = _orphan(world)

    resp = world.as_user(world.people.author).get(f"/media/{asset_id}/file")

    assert resp.status_code == 200 and resp.headers["content-type"] == "image/png"
    world.db.expire_all()
    assert str(world.db.get(MediaAsset, UUID(asset_id)).tenant_id) == world.people.author["tenant_id"]


def test_an_old_picture_without_a_business_is_not_open_to_other_staff(world):
    asset_id = _orphan(world)
    other = {**world.people.author, "id": str(__import__("uuid").uuid4())}

    assert world.as_user(other).get(f"/media/{asset_id}/file").status_code == 404


def test_the_backfill_takes_the_business_from_the_uploaders_other_pictures(world):
    kept, orphan = upload(world), _orphan(world)

    dry = media_service.backfill_tenants(world.db)
    assert dry["resolved"] == 1 and dry["written"] == 0 and world.db.get(MediaAsset, UUID(orphan)).tenant_id is None
    done = media_service.backfill_tenants(world.db, apply=True, asset_id=orphan)

    assert done["written"] == 1 and str(world.db.get(MediaAsset, UUID(orphan)).tenant_id) == world.people.author["tenant_id"] and kept
