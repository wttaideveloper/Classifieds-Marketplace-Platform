"""Blog media: video uploads, replace and delete, file details and the list, signed download links, range requests, the declared-type check, and a
field's own file limits checked against the real file."""
import pytest

from app.core.config import settings
from tests.blog_support import API, draft, live_merchant_post, make_category, review, submit, world  # noqa: F401
from tests.test_blog_forms_and_posts import make_form, png, publish_form, root, upload, uploads_dir  # noqa: F401

MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 3000
MOV = b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  " + b"\x00" * 3000
WEBM = b"\x1a\x45\xdf\xa3\x9fB\x86\x81\x01B\xf7\x81\x01B\xf2\x81\x04B\xf3\x81\x08B\x82\x84webm" + b"\x00" * 3000


def video(w, user, name="clip.mp4", data=MP4, content_type="video/mp4"):
    return upload(w, user, name, data, content_type, "video")


VIDEO_FORM = {"sections": [
    {"key": "basics", "title": "Basics", "fields": [{"key": "title"}, {"key": "category"}, {"key": "content"}, {"key": "excerpt"}]},
    {"key": "media", "title": "Media", "fields": [
        {"key": "clip", "label": "Clip", "type": "video", "validation": {"max_items": 2, "max_file_size_mb": 0.1, "allowed_types": ["webm"]}},
        {"key": "tour", "label": "Tour", "type": "video", "validation": {"max_items": 2}},
        {"key": "pics", "label": "Pictures", "type": "additional_images", "validation": {"max_items": 3, "allowed_types": ["png"]}}]}]}


# --- uploading videos ---------------------------------------------------------------------------------------------------------

def test_a_video_is_checked_by_its_content_and_stored_as_it_is(world, uploads_dir):
    asha = world.people.asha
    for name, data, mime in (("a.mp4", MP4, "video/mp4"), ("b.mov", MOV, "video/quicktime"), ("c.webm", WEBM, "video/webm")):
        resp = video(world, asha, name, data, mime)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["purpose"] == "video" and body["content_type"] == mime and body["name"] == name and body["size"] == len(data) and body["width"] is None
        assert body["url"].endswith(f"/files/{body['file_id']}") and body["scan_status"]
    assert len(list((uploads_dir / "blogs").iterdir())) == 3
    assert world.as_user(asha).get(f"{API}/uploads/limits").json()["video_extensions"] == ["mov", "mp4", "webm"]
    assert world.as_user(asha).get(f"{API}/uploads/limits").json()["video_max_mb"] == settings.BLOG_MAX_VIDEO_MB


@pytest.mark.parametrize("name, data, mime, code", [
    ("fake.mp4", b"this is not a video at all, just text" * 20, "video/mp4", "UNSUPPORTED_TYPE"),
    ("clip.avi", MP4, "video/x-msvideo", "UNSUPPORTED_TYPE"),
    ("clip.mov", MP4, "video/quicktime", "UNSUPPORTED_TYPE"),                      # an MP4 named .mov
    ("clip.webm", MP4, "video/webm", "UNSUPPORTED_TYPE"),
    ("clip.mp4", MP4, "image/png", "MIME_MISMATCH"),                              # says it is a picture
    ("run.mp4", b"MZ\x90\x00" + b"\x00" * 200, "video/mp4", "UNSUPPORTED_TYPE"),   # a program with a video name
])
def test_a_file_that_is_not_the_video_it_claims_to_be_is_refused(world, uploads_dir, name, data, mime, code):
    resp = video(world, world.people.asha, name, data, mime)
    assert resp.status_code == 415 and resp.json()["code"] == code
    assert not (uploads_dir / "blogs").exists() or not list((uploads_dir / "blogs").iterdir())


def test_size_limits_and_the_declared_type_apply_to_every_kind_of_upload(world, uploads_dir, monkeypatch):
    asha = world.people.asha
    monkeypatch.setattr(settings, "BLOG_MAX_VIDEO_MB", 0)
    big = video(world, asha)
    assert big.status_code == 413 and big.json()["code"] == "FILE_TOO_LARGE" and big.json()["max_mb"] == 0
    monkeypatch.undo()
    assert upload(world, asha, "a.png", png(), "application/pdf", "image").json()["code"] == "MIME_MISMATCH"       # a picture that says it is a PDF
    assert upload(world, asha, "a.png", png(), "application/octet-stream", "image").status_code == 201               # no useful claim: judged by content
    assert upload(world, asha, "a.mp4", MP4, "image/png", "image").status_code == 415


def test_an_infected_video_is_blocked(world, uploads_dir, monkeypatch):
    from fastapi import HTTPException

    def infected(data):
        raise HTTPException(status_code=422, detail="The file was blocked by the virus scan")

    monkeypatch.setattr("app.services.blog_upload_service.virus_scan.scan_or_refuse", infected)
    resp = video(world, world.people.asha)
    assert resp.status_code == 422 and resp.json()["code"] == "VIRUS_DETECTED"


# --- details, list, delete, replace ---------------------------------------------------------------------------------------------

def test_the_list_and_the_details_show_each_file_and_where_it_is_used(world, uploads_dir):
    asha, bela = world.people.asha, world.people.bela
    cat = make_category(world)
    pic = upload(world, asha, "a.png", png(), "image/png", "image").json()
    clip = video(world, asha).json()
    other = upload(world, bela, "b.png", png(), "image/png", "image").json()
    post = draft(world, asha, cat, cover_image_url=pic["url"])

    listing = world.as_user(asha).get(f"{API}/uploads").json()
    assert listing["pagination"]["total"] == 2 and {i["file_id"] for i in listing["items"]} == {pic["file_id"], clip["file_id"]}
    assert [i["purpose"] for i in world.as_user(asha).get(f"{API}/uploads", params={"purpose": "video"}).json()["items"]] == ["video"]
    assert world.as_user(root(world) and world.people.root).get(f"{API}/uploads").json()["pagination"]["total"] == 3          # a Super Admin sees all

    detail = world.as_user(asha).get(f"{API}/uploads/{pic['file_id']}").json()
    assert detail["sha256"] and detail["scan_status"] and detail["width"] == 20 and detail["replaced_at"] is None
    assert detail["used_by"] == [{"post_id": post["id"], "title": post["title"], "status": "draft", "is_live": False}]
    assert world.as_user(asha).get(f"{API}/uploads/{clip['file_id']}").json()["used_by"] == []
    assert world.as_user(asha).get(f"{API}/uploads/{other['file_id']}").json()["code"] == "FILE_NOT_FOUND"                      # another business's file
    assert world.as_user(world.people.priya).get(f"{API}/uploads").status_code == 403 and world.as_user(None).get(f"{API}/uploads").status_code == 401


def test_a_file_in_use_cannot_be_deleted_and_an_unused_one_can(world, uploads_dir):
    asha, bela = world.people.asha, world.people.bela
    cat = make_category(world)
    pic = upload(world, asha, "a.png", png(), "image/png", "image").json()
    spare = upload(world, asha, "b.png", png(), "image/png", "image").json()
    post = draft(world, asha, cat, cover_image_url=pic["url"])

    refused = world.as_user(asha).delete(f"{API}/uploads/{pic['file_id']}")
    assert refused.status_code == 409 and refused.json()["code"] == "FILE_IN_USE" and refused.json()["used_by"][0]["post_id"] == post["id"]
    assert world.as_user(bela).delete(f"{API}/uploads/{spare['file_id']}").status_code == 404                                    # not theirs

    assert world.as_user(asha).delete(f"{API}/uploads/{spare['file_id']}").status_code == 200
    assert world.as_user(asha).get(f"{API}/uploads/{spare['file_id']}").status_code == 404
    assert world.as_user(asha).get(f"{API}/files/{spare['file_id']}").status_code == 404
    assert len(list((uploads_dir / "blogs").iterdir())) == 1                                                                    # the bytes are gone
    assert world.as_user(asha).get(f"{API}/uploads").json()["pagination"]["total"] == 1

    world.as_user(asha).patch(f"{API}/my/posts/{post['id']}", json={"cover_image_url": None})
    assert world.as_user(asha).delete(f"{API}/uploads/{pic['file_id']}").status_code == 200                                      # no longer used
    taken = world.as_user(asha).post(f"{API}/my/posts", json={"title": "Reuse", "cover_image_url": spare["url"]})
    assert taken.status_code == 422 and taken.json()["errors"][0]["code"] == "FILE_NOT_FOUND"                                   # a deleted file cannot be attached


def test_replacing_a_file_keeps_its_address_and_is_checked_like_a_new_upload(world, uploads_dir):
    asha = world.people.asha
    pic = upload(world, asha, "old.png", png((20, 20)), "image/png", "image").json()
    before = world.as_user(asha).get(f"{API}/files/{pic['file_id']}").content

    done = world.as_user(asha).put(f"{API}/uploads/{pic['file_id']}", files={"file": ("new.png", png((40, 30)), "image/png")})
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["file_id"] == pic["file_id"] and body["url"] == pic["url"] and body["name"] == "new.png" and body["width"] == 40 and body["height"] == 30
    assert world.as_user(asha).get(f"{API}/files/{pic['file_id']}").content != before
    assert world.as_user(asha).get(f"{API}/uploads/{pic['file_id']}").json()["replaced_at"]
    assert len(list((uploads_dir / "blogs").iterdir())) == 1                                                                    # the old bytes were removed

    bad = world.as_user(asha).put(f"{API}/uploads/{pic['file_id']}", files={"file": ("x.png", b"not an image", "image/png")})
    assert bad.status_code == 415 and bad.json()["code"] == "UNSUPPORTED_TYPE"
    wrong_kind = world.as_user(asha).put(f"{API}/uploads/{pic['file_id']}", files={"file": ("a.mp4", MP4, "video/mp4")})
    assert wrong_kind.status_code == 415                                                                                         # a picture slot takes pictures
    assert world.as_user(world.people.bela).put(f"{API}/uploads/{pic['file_id']}", files={"file": ("x.png", png(), "image/png")}).status_code == 404

    clip = video(world, asha).json()
    swapped = world.as_user(asha).put(f"{API}/uploads/{clip['file_id']}", files={"file": ("c.webm", WEBM, "video/webm")})
    assert swapped.status_code == 200 and swapped.json()["content_type"] == "video/webm" and swapped.json()["url"] == clip["url"]


def test_a_file_a_published_post_uses_cannot_be_replaced_without_approval(world, uploads_dir):
    asha = world.people.asha
    cat = make_category(world)
    pic = upload(world, asha, "a.png", png(), "image/png", "image").json()
    live = live_merchant_post(world, cat, cover_image_url=pic["url"])

    refused = world.as_user(asha).put(f"{API}/uploads/{pic['file_id']}", files={"file": ("b.png", png((30, 30)), "image/png")})
    assert refused.status_code == 409 and refused.json()["code"] == "FILE_IN_USE_LIVE"
    fresh = upload(world, asha, "b.png", png((30, 30)), "image/png", "image").json()
    edit = world.as_user(asha).patch(f"{API}/my/posts/{live['id']}", json={"cover_image_url": fresh["url"]})
    assert edit.status_code == 200 and edit.json()["status"] == "pending_approval"                                              # the change goes through approval
    assert world.as_user(None).get(f"{API}/{live['slug']}").json()["cover_image_url"] == pic["url"]
    assert world.as_user(None).get(f"{API}/files/{fresh['file_id']}").status_code == 404                                         # the new file is not public yet
    assert world.as_user(asha).delete(f"{API}/uploads/{pic['file_id']}").status_code == 409                                      # and the old one is still in use


# --- signed links and range requests ----------------------------------------------------------------------------------------------

def test_a_signed_link_gives_temporary_access_and_nothing_else(world, uploads_dir, monkeypatch):
    asha = world.people.asha
    pic = upload(world, asha, "a.png", png(), "image/png", "image").json()
    path = f"{API}/files/{pic['file_id']}"
    assert world.as_user(None).get(path).status_code == 404

    link = world.as_user(asha).post(f"{API}/uploads/{pic['file_id']}/signed-url").json()
    assert link["url"].startswith(pic["url"] + "?expires=") and "&signature=" in link["url"] and link["expires_at"]
    query = link["url"].split("?", 1)[1]
    anyone = world.as_user(None).get(f"{path}?{query}")
    assert anyone.status_code == 200 and anyone.headers["content-type"] == "image/png"

    expires, signature = (part.split("=", 1)[1] for part in query.split("&"))
    for wrong in (f"expires={expires}&signature={'0' * 40}", f"expires={int(expires) + 60}&signature={signature}", f"expires={expires}", "signature=abc", "expires=abc&signature=abc"):
        assert world.as_user(None).get(f"{path}?{wrong}").status_code == 404
    other = upload(world, asha, "b.png", png(), "image/png", "image").json()
    assert world.as_user(None).get(f"{API}/files/{other['file_id']}?{query}").status_code == 404                                  # a link is for one file
    monkeypatch.setattr("app.services.blog_upload_service.time.time", lambda: float(expires) + 5)
    assert world.as_user(None).get(f"{path}?{query}").status_code == 404                                                           # expired
    assert world.as_user(world.people.bela).post(f"{API}/uploads/{pic['file_id']}/signed-url").status_code == 404                # only for a file you manage


def test_videos_are_served_inline_with_range_support(world, uploads_dir):
    asha = world.people.asha
    clip = video(world, asha).json()
    got = world.as_user(asha).get(f"{API}/files/{clip['file_id']}", headers={"Range": "bytes=0-7"})
    assert got.status_code == 206 and got.content == MP4[:8] and got.headers["content-range"].startswith("bytes 0-7/") and got.headers["accept-ranges"] == "bytes"
    assert got.headers["content-type"] == "video/mp4" and got.headers["content-disposition"].startswith("inline") and got.headers["x-content-type-options"] == "nosniff"
    assert world.as_user(asha).get(f"{API}/files/{clip['file_id']}").content == MP4


# --- files in a form's fields -------------------------------------------------------------------------------------------------------

def test_a_video_field_takes_the_businesss_own_video_and_checks_the_real_file_against_its_rules(world, uploads_dir):
    publish_form(world, make_form(world, definition=VIDEO_FORM))
    cat = make_category(world)
    asha, bela = world.people.asha, world.people.bela
    mp4 = video(world, asha).json()
    webm = video(world, asha, "c.webm", WEBM, "video/webm").json()
    big_webm = video(world, asha, "d.webm", WEBM + b"\x00" * 120_000, "video/webm").json()
    pic = upload(world, asha, "a.png", png(), "image/png", "image").json()
    post = draft(world, asha, cat)

    def patch(values, user=asha, post_id=post["id"]):
        return world.as_user(user).patch(f"{API}/my/posts/{post_id}", json={"custom_fields": values})

    ok = patch({"tour": [mp4["url"], "https://www.youtube.com/watch?v=dQw4w9WgXcQ"]})
    assert ok.status_code == 200, ok.text
    tour = ok.json()["custom_fields"]["tour"]
    assert [v["provider"] for v in tour] == ["upload", "youtube"] and tour[0]["name"] == "clip.mp4" and tour[0]["content_type"] == "video/mp4" and tour[0]["size"] == mp4["size"]

    assert patch({"clip": [webm["url"]]}).status_code == 200                                                                      # webm, under the field's size limit
    wrong_type = patch({"clip": [mp4["url"]]})
    assert wrong_type.status_code == 422 and wrong_type.json()["errors"][0]["code"] == "FILE_TYPE_NOT_ALLOWED"                    # the field takes webm only
    too_big = patch({"clip": [big_webm["url"]]})
    assert too_big.status_code == 422 and too_big.json()["errors"][0]["code"] == "FILE_TOO_LARGE"                                  # over 0.1 MB: the real size, not a claim
    lying = patch({"clip": [{"url": webm["url"], "size": 1, "content_type": "video/webm", "name": "tiny.webm"}]})
    assert lying.status_code == 200 and lying.json()["custom_fields"]["clip"][0]["size"] == webm["size"] and lying.json()["custom_fields"]["clip"][0]["name"] == "c.webm"

    assert patch({"tour": [pic["url"]]}).json()["errors"][0]["code"] == "WRONG_FILE_KIND"                                          # a picture in a video field
    assert patch({"pics": [mp4["url"]]}).json()["errors"][0]["code"] == "WRONG_FILE_KIND"
    other_post = draft(world, bela, cat)
    assert patch({"tour": [mp4["url"]]}, bela, other_post["id"]).json()["errors"][0]["code"] == "FILE_NOT_FOUND"                  # not their business's file


def test_an_uploaded_video_is_public_only_through_a_live_post_and_never_shows_internal_ids(world, uploads_dir):
    publish_form(world, make_form(world, definition=VIDEO_FORM))
    cat = make_category(world)
    asha = world.people.asha
    mp4 = video(world, asha).json()
    path = f"{API}/files/{mp4['file_id']}"
    post = draft(world, asha, cat, custom_fields={"tour": [mp4["url"]]})
    assert world.as_user(None).get(path).status_code == 404
    submit(world, asha, post)
    live = review(world, post, "approve").json()

    public = world.as_user(None).get(f"{API}/{live['slug']}").json()
    tour = next(f for f in public["custom_fields"] if f["key"] == "tour")
    assert tour["type"] == "video" and tour["renderer"] == "video_input" and tour["value"][0]["provider"] == "upload" and "file_id" not in str(public)
    assert world.as_user(None).get(path, headers={"Range": "bytes=0-3"}).status_code == 206                                       # a reader can play it
    assert world.as_user(asha).delete(f"{API}/uploads/{mp4['file_id']}").status_code == 409
    world.as_user(root(world) and world.people.root).post(f"{API}/admin/posts/{live['id']}/unpublish", json={"reason": "Pulled"})
    assert world.as_user(None).get(path).status_code == 404
