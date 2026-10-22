"""eBook authoring: the structure that is stored, saving with version checks, reordering, validation, and who may do what."""
import json

import pytest

from app.models.ebook_model import MediaAsset, TrainingEbook
from tests.ebook_support import LESSON, SECTION, TENANT_B, decide, doc, image_block, notices_of, page, person, save, saved, submit, upload, world  # noqa: F401


# --- the structure is kept exactly ------------------------------------------------------------------------------------------------------

def test_a_new_lesson_starts_as_an_empty_draft(world):
    resp = world.as_user(world.people.author).get(world.base)

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] is None and body["version"] == 0 and body["status"] == "draft" and body["ebook"]["pages"] == []
    assert body["ebook"]["completion_rule"] == "read_to_end" and body["ebook"]["allow_bookmarks"] is True and body["ebook"]["allow_notes"] is True
    assert body["actions"] == ["save", "reorder", "validate"] and body["has_unpublished_changes"] is False


def test_saving_returns_the_id_version_status_and_time(world):
    out = saved(world)

    assert set(out) == {"id", "lesson_id", "version", "status", "updated_at", "published_version", "has_unpublished_changes", "warnings"}
    assert out["lesson_id"] == LESSON and out["version"] == 1 and out["status"] == "draft" and out["warnings"] == [] and out["has_unpublished_changes"] is True
    assert saved(world)["version"] == 2 and saved(world)["id"] == out["id"]


def test_every_page_and_picture_field_comes_back_exactly_as_saved(world):
    asset = upload(world, size=(1600, 900))
    html = "<h2>Introduction</h2><p>Nutrition is <strong>the study</strong> of <em>food</em>.</p><ul><li>Protein</li><li>Fibre</li></ul>"
    block = image_block(asset, "image_block_1", width="medium", fit="contain", aspect_ratio="4:3", position="top left", caption="Examples of healthy food", placement="before_content")
    saved(world, doc([page("page_1", "Introduction", html, [block]), page("page_2", "Food groups")], subtitle="An introduction to nutrition", author="Invigorate Health",
                     description="Book description", cover_asset_id=asset, completion_rule="read_to_end", allow_bookmarks=False, allow_notes=True))

    body = world.as_user(world.people.author).get(world.base).json()["ebook"]

    assert body["subtitle"] == "An introduction to nutrition" and body["author"] == "Invigorate Health" and body["description"] == "Book description"
    assert body["cover_asset_id"] == asset and body["cover_url"].endswith(f"/api/v1/media/{asset}/file")
    assert body["completion_rule"] == "read_to_end" and body["allow_bookmarks"] is False and body["allow_notes"] is True
    first, second = body["pages"]
    assert (first["id"], first["order"], first["title"]) == ("page_1", 1, "Introduction") and (second["id"], second["order"]) == ("page_2", 2)
    assert first["content_html"] == html
    [image] = first["images"]
    for key in ("id", "asset_id", "alt_text", "caption", "width", "fit", "aspect_ratio", "position", "decorative", "placement"):
        assert image[key] == {**block, "decorative": False}[key], key
    assert image["natural_width"] == 1600 and image["natural_height"] == 900 and image["available"] is True and image["url"].endswith(f"/api/v1/media/{asset}/file")


def test_rich_formatting_tables_alignment_and_picture_placeholders_survive(world):
    html = ('<h3>Plan</h3><p style="text-align: center; color: #336699">Centred <mark>highlight</mark> H<sub>2</sub>O x<sup>2</sup></p>'
            '<table><thead><tr><th>Food</th><th>Protein</th></tr></thead><tbody><tr><td colspan="2">Eggs</td></tr></tbody></table>'
            '<div data-image-id="image_block_1"></div><blockquote>Eat well</blockquote><p><a href="https://example.org/guide">guide</a></p>')
    saved(world, doc([page("page_1", "Plan", html, [image_block(upload(world), placement="inline")])]))

    stored = world.as_user(world.people.author).get(world.base).json()["ebook"]["pages"][0]["content_html"]

    for expected in ('style="text-align: center; color: #336699"', "<mark>highlight</mark>", "<sub>2</sub>", "<sup>2</sup>", "<table>", "<thead>", 'colspan="2"',
                     'data-image-id="image_block_1"', "<blockquote>Eat well</blockquote>", 'href="https://example.org/guide"'):
        assert expected in stored, expected


def test_unsafe_markup_is_removed_and_reported(world):
    dirty = ('<p onclick="x()" style="position:fixed; background:url(http://evil/x); color: red">Hi <script>alert(1)</script>'
             '<a href="javascript:alert(1)">bad</a><img src="https://tracker.example/p.png"><iframe src="https://evil"></iframe></p>')

    out = saved(world, doc([page("page_1", "Dirty", dirty)]))

    stored = world.as_user(world.people.author).get(world.base).json()["ebook"]["pages"][0]
    html = stored["content_html"].lower()
    for forbidden in ("script", "onclick", "javascript", "iframe", "<img", "position", "url(", "tracker"):
        assert forbidden not in html, forbidden
    assert "color: red" in html and "hi" in html
    [warning] = out["warnings"]
    assert warning["page_id"] == "page_1" and warning["code"] == "unsafe_markup_removed" and "<script>" in warning["message"] and "event handler" in warning["message"]
    assert stored["html_notes"]                                                                            # kept for the editor and the reviewer


def test_ids_and_order_are_filled_in_and_normalised(world):
    saved(world, doc([{"title": "Second", "order": 2, "content_html": "<p>b</p>"}, {"title": "First", "order": 1, "content_html": "<p>a</p>", "images": []}]))

    pages = world.as_user(world.people.author).get(world.base).json()["ebook"]["pages"]

    assert [p["title"] for p in pages] == ["First", "Second"] and [p["order"] for p in pages] == [1, 2] and all(p["id"].startswith("page_") for p in pages)
    saved(world, doc([page("a", "A"), page("b", "B"), page("c", "C")]))
    assert [p["order"] for p in world.as_user(world.people.author).get(world.base).json()["ebook"]["pages"]] == [1, 2, 3]       # list order when no order is sent


@pytest.mark.parametrize("bad", [
    {"pages": [{"id": "p1", "title": "A", "oops": 1}]},                                  # an unknown field is an error, not silently dropped
    {"pages": [{"id": "p1", "title": "A", "images": [{"asset_id": "x", "alt_txt": "typo"}]}]},
    {"pages": [{"id": "p1", "title": "A", "images": [{"asset_id": "x", "width": "huge"}]}]},
    {"pages": [{"id": "p1", "title": "A", "images": [{"asset_id": "x", "aspect_ratio": "wide"}]}]},
    {"pages": [{"id": "p1", "title": "A", "images": [{"asset_id": "x", "position": "middle"}]}]},
    {"pages": [{"id": "bad id!", "title": "A"}]},
    {"completion_rule": "whenever"},
    {"pages": [{"id": "p1", "title": "x" * 201}]},
])
def test_malformed_content_is_refused_with_422(world, bad):
    assert save(world, bad).status_code == 422


def test_duplicate_ids_and_oversize_pages_are_refused(world, monkeypatch):
    assert save(world, doc([page("p1", "A"), page("p1", "B")])).status_code == 422
    assert save(world, doc([page("p1", "A", images=[image_block("x", "i1"), image_block("y", "i1")])])).status_code == 422
    from app.core.config import settings

    monkeypatch.setattr(settings, "EBOOK_MAX_PAGES", 2)
    assert save(world, doc([page("a", "A"), page("b", "B"), page("c", "C")])).status_code == 422
    monkeypatch.setattr(settings, "EBOOK_MAX_PAGE_HTML_BYTES", 50)
    assert save(world, doc([page("a", "A", "<p>" + "x" * 100 + "</p>")])).status_code == 422
    monkeypatch.setattr(settings, "EBOOK_MAX_IMAGES_PER_PAGE", 1)
    assert save(world, doc([page("a", "A", images=[image_block("x", "i1"), image_block("y", "i2")])])).status_code == 422


# --- version conflicts ---------------------------------------------------------------------------------------------------------------------------

def test_a_stale_save_is_refused_instead_of_overwriting_someone_elses_work(world):
    first = saved(world)
    other = world.people.provider
    saved(world, doc([page("page_1", "Provider's change")]), user=other, base_version=first["version"])

    stale = save(world, doc([page("page_1", "My change")]), base_version=first["version"])

    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["code"] == "VERSION_CONFLICT" and detail["current_version"] == 2 and detail["your_version"] == 1 and detail["updated_by"] == "Dr Provider"
    now = world.as_user(world.people.author).get(world.base).json()
    assert now["ebook"]["pages"][0]["title"] == "Provider's change" and now["version"] == 2 and now["updated_by"]["name"] == "Dr Provider"
    assert save(world, doc([page("page_1", "Merged")]), base_version=2).status_code == 200


def test_the_first_save_needs_version_zero_or_none(world):
    assert save(world, doc(), base_version=5).status_code == 409
    assert save(world, doc(), base_version=0).status_code == 200


# --- reorder ------------------------------------------------------------------------------------------------------------------------------------------

def test_pages_are_reordered_and_renumbered(world):
    saved(world, doc([page("page_1", "One"), page("page_2", "Two"), page("page_3", "Three")]))

    resp = world.as_user(world.people.author).patch(world.base + "/pages/reorder", json={"page_ids": ["page_3", "page_1", "page_2"]})

    assert resp.status_code == 200 and resp.json()["version"] == 2 and resp.json()["page_ids"] == ["page_3", "page_1", "page_2"]
    pages = world.as_user(world.people.author).get(world.base).json()["ebook"]["pages"]
    assert [(p["id"], p["order"]) for p in pages] == [("page_3", 1), ("page_1", 2), ("page_2", 3)]
    assert pages[0]["content_html"].startswith("<h2>Three</h2>")                         # content travels with the page


def test_a_reorder_must_name_every_page_once(world):
    saved(world, doc([page("page_1", "One"), page("page_2", "Two")]))
    author = world.as_user(world.people.author)

    for ids in (["page_1"], ["page_1", "page_1"], ["page_1", "page_2", "page_3"], ["page_1", "nope"]):
        assert author.patch(world.base + "/pages/reorder", json={"page_ids": ids}).status_code == 422, ids
    assert author.patch(world.base + "/pages/reorder", json={"page_ids": ["page_2", "page_1"], "base_version": 99}).status_code == 409
    assert author.get(world.base).json()["version"] == 1


def test_reordering_before_anything_is_saved_is_404(world):
    assert world.as_user(world.people.author).patch(world.base + "/pages/reorder", json={"page_ids": ["a"]}).status_code == 404


# --- validation ------------------------------------------------------------------------------------------------------------------------------------------

def validate(world, body=None, user=None):
    resp = world.as_user(user or world.people.author).post(world.base + "/validate", json=body if body is not None else {})
    assert resp.status_code == 200, resp.text
    return resp.json()


def codes(report, key="errors"):
    return sorted((e["code"], e["page_id"], e["image_id"]) for e in report[key])


def test_a_good_ebook_is_valid(world):
    saved(world, doc([page("page_1", "One", images=[image_block(upload(world))])]))

    report = validate(world)

    assert report == {"valid": True, "errors": [], "warnings": [], "page_count": 1, "version": 1}


def test_an_empty_ebook_has_no_pages_error(world):
    assert codes(validate(world)) == [("no_pages", None, None)]
    saved(world, doc([]))
    assert codes(validate(world)) == [("no_pages", None, None)]


def test_each_rule_reports_the_page_and_field(world):
    asset = upload(world)
    saved(world, doc([
        page("p_no_title", "", "<p>text</p>"),
        page("p_empty", "Empty", "<p></p>"),
        page("p_alt", "Alt", "<p>x</p>", [image_block(asset, "img_noalt", alt_text="")]),
        page("p_ok", "Fine", "<p>x</p>", [image_block(asset, "img_ok")]),
        page("p_decor", "Decor", "<p>x</p>", [image_block(asset, "img_decor", alt_text="", decorative=True)]),
        page("p_missing", "Missing asset", "<p>x</p>", [image_block("00000000-0000-4000-8000-000000000000", "img_missing")]),
    ]))

    report = validate(world)

    assert report["valid"] is False
    assert codes(report) == sorted([("page_title_missing", "p_no_title", None), ("page_empty", "p_empty", None), ("alt_text_required", "p_alt", "img_noalt"),
                                    ("asset_unavailable", "p_missing", "img_missing")])
    alt = next(e for e in report["errors"] if e["code"] == "alt_text_required")
    assert alt["field"] == "alt_text" and alt["message"] == "Image alt text is required"
    assert next(e for e in report["errors"] if e["code"] == "page_title_missing")["field"] == "title"


def test_pictures_must_exist_belong_to_this_business_and_still_have_their_file(world):
    mine, deleted, gone = upload(world), upload(world), upload(world)
    foreign = upload(world, user=world.people.outsider)
    world.as_user(world.people.author).delete(f"/media/{deleted}")
    world.db.query(MediaAsset).filter_by(id=__import__("uuid").UUID(gone)).one()
    (world.tmp / world.db.query(MediaAsset).filter_by(id=__import__("uuid").UUID(gone)).one().stored_name).unlink()
    blocks = [image_block(a, f"i{n}") for n, a in enumerate([mine, deleted, foreign, gone])]
    saved(world, doc([page("p1", "One", images=blocks)], cover_asset_id="not-a-uuid"))

    report = validate(world)

    bad = {e["image_id"]: e["message"] for e in report["errors"] if e["code"] == "asset_unavailable"}
    assert set(bad) == {"i1", "i2", "i3"}
    assert "does not exist" in bad["i1"] and "another business" in bad["i2"] and "missing from storage" in bad["i3"]
    assert any(e["code"] == "cover_unavailable" for e in report["errors"])


def test_text_that_still_carries_unsupported_markup_is_reported(world):
    saved(world, doc([page("p1", "One", "<p>ok</p><script>x()</script>")]))

    report = validate(world)

    assert [e["code"] for e in report["errors"]] == ["invalid_html"] and "<script>" in report["errors"][0]["message"]
    saved(world, doc([page("p1", "One", "<p>ok</p>")]))                                       # saved again from a clean editor
    assert validate(world)["valid"] is True


def test_an_inline_picture_without_a_place_in_the_text_is_a_warning_not_an_error(world):
    asset = upload(world)
    saved(world, doc([page("p1", "One", '<p>x</p><div data-image-id="ghost"></div>', [image_block(asset, "i1", placement="inline")])]))

    report = validate(world)

    assert report["valid"] is True
    assert codes(report, "warnings") == sorted([("inline_placeholder_missing", "p1", "i1"), ("unknown_placeholder", "p1", None)])


def test_unsaved_content_can_be_checked_without_saving(world):
    report = validate(world, {"ebook": doc([page("p1", "", "<p>x</p>")])})

    assert codes(report) == [("page_title_missing", "p1", None)] and report["version"] is None
    assert world.db.query(TrainingEbook).count() == 0


# --- sending for approval -----------------------------------------------------------------------------------------------------------------------------

def test_an_invalid_ebook_cannot_be_sent_and_the_problems_are_listed(world):
    saved(world, doc([page("p1", "", "<p>x</p>")]))

    resp = submit(world)

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "EBOOK_INVALID" and detail["errors"][0]["code"] == "page_title_missing"
    assert world.as_user(world.people.author).get(world.base).json()["status"] == "draft"


def test_sending_for_approval_locks_the_draft_and_tells_the_super_admins(world):
    saved(world)

    sent = submit(world).json()

    assert sent["status"] == "pending_approval" and sent["status_label"] == "Pending Approval" and sent["submitted_at"] and sent["actions"] == ["validate", "withdraw", "history"]
    [notice] = notices_of(world, "ebook_submitted")
    assert [str(u) for u in notice["user_ids"]] == [world.people.root["id"]] and notice["metadata"]["lesson_title"] == "Nutrition basics"
    assert notice["metadata"]["training_title"] == "Nutrition 101" and notice["metadata"]["entity_type"] == "ebook" and "Nutrition basics" in notice["message"]
    locked = save(world, doc([page("page_1", "Changed")]))
    assert locked.status_code == 409 and locked.json()["detail"]["code"] == "IN_REVIEW"
    assert world.as_user(world.people.author).patch(world.base + "/pages/reorder", json={"page_ids": ["page_2", "page_1"]}).status_code == 409
    assert submit(world).status_code == 409                                                  # already waiting


def test_a_withdrawn_ebook_can_be_edited_again(world):
    saved(world)
    submit(world)

    back = world.as_user(world.people.author).post(world.base + "/withdraw").json()

    assert back["status"] == "draft" and save(world, doc([page("page_1", "Edited")])).status_code == 200
    assert world.as_user(world.people.author).post(world.base + "/withdraw").status_code == 409


# --- the decision --------------------------------------------------------------------------------------------------------------------------------------------

def test_changes_need_a_note_the_author_can_read_and_are_notified(world):
    saved(world)
    submit(world)

    assert decide(world, "request_changes").status_code == 422
    assert decide(world, "request_changes", "no").status_code == 422
    done = decide(world, "request_changes", "Please add alt text to the diagram")

    assert done.status_code == 200 and done.json()["status"] == "changes_requested" and done.json()["reviewer_note"] == "Please add alt text to the diagram"
    [notice] = notices_of(world, "ebook_changes_requested")
    assert [str(u) for u in notice["user_ids"]] == [world.people.author["id"]] and notice["metadata"]["reason"] == "Please add alt text to the diagram"
    mine = world.as_user(world.people.author).get(world.base).json()
    assert mine["status"] == "changes_requested" and mine["reviewer_note"] == "Please add alt text to the diagram" and "submit" in mine["actions"]
    assert save(world, doc([page("page_1", "Fixed")])).status_code == 200                      # editing is allowed
    assert world.as_user(world.people.author).get(world.base).json()["status"] == "changes_requested"        # until it is sent again
    assert submit(world).status_code == 200
    assert world.as_user(world.people.author).get(world.base).json()["reviewer_note"] is None


def test_approving_publishes_by_default_and_tells_the_author(world):
    saved(world)
    submit(world)

    done = decide(world).json()

    assert done["status"] == "approved" and done["published_version"] == 1 and done["version"] == 1 and done["validation"]["valid"] is True
    [notice] = notices_of(world, "ebook_approved")
    assert [str(u) for u in notice["user_ids"]] == [world.people.author["id"]] and notice["metadata"]["published"] is True and "now live" in notice["message"]
    mine = world.as_user(world.people.author).get(world.base).json()
    assert mine["published_version"] == 1 and mine["has_unpublished_changes"] is False and mine["published_at"] and "unpublish" in mine["actions"]


def test_an_approval_can_wait_for_the_owner_to_publish(world):
    saved(world)
    submit(world)

    done = decide(world, publish=False).json()

    assert done["status"] == "approved" and done["published_version"] is None and done["published"] is None
    assert notices_of(world, "ebook_approved")[0]["metadata"]["published"] is False
    mine = world.as_user(world.people.author).get(world.base).json()
    assert "publish" in mine["actions"]
    live = world.as_user(world.people.author).post(world.base + "/publish").json()
    assert live["published_version"] == 1 and live["has_unpublished_changes"] is False
    assert world.as_user(world.people.author).post(world.base + "/publish").status_code == 409


def test_nothing_unapproved_can_be_published(world):
    saved(world)

    for user in (world.people.author, world.people.root):
        assert world.as_user(user).post(world.base + "/publish").status_code == 409
    submit(world)
    assert world.as_user(world.people.author).post(world.base + "/publish").status_code == 409
    decide(world, "request_changes", "Not yet")
    assert world.as_user(world.people.author).post(world.base + "/publish").status_code == 409


def test_only_a_pending_ebook_can_be_decided(world):
    saved(world)

    assert decide(world).status_code == 409
    submit(world)
    decide(world)
    assert decide(world).status_code == 409                                                       # decided already


def test_approval_rechecks_the_ebook(world):
    asset = upload(world)
    saved(world, doc([page("p1", "One", images=[image_block(asset)])]))
    submit(world)
    (world.tmp / world.db.query(MediaAsset).one().stored_name).unlink()                                # the picture's file vanished while it waited

    resp = decide(world)

    assert resp.status_code == 422 and resp.json()["detail"]["errors"][0]["code"] == "asset_unavailable"
    assert world.as_user(world.people.root).get(world.admin_base, params={"include_drafts": "true"}).json()["status"] == "pending_approval"


def test_a_change_after_approval_does_not_touch_what_is_live(world):
    saved(world, doc([page("p1", "Original")]))
    submit(world)
    decide(world)

    edited = saved(world, doc([page("p1", "Edited")]))

    assert edited["status"] == "draft" and edited["version"] == 2 and edited["published_version"] == 1 and edited["has_unpublished_changes"] is True
    live = world.as_user(world.people.root).get(world.admin_base).json()
    assert live["published"]["pages"][0]["title"] == "Original" and live["draft"] is None                       # without include_drafts only what is live
    assert submit(world).status_code == 200
    decide(world)
    assert world.as_user(world.people.root).get(world.admin_base).json()["published"]["pages"][0]["title"] == "Edited"


def test_sending_the_live_version_again_is_refused(world):
    saved(world)
    submit(world)
    decide(world)

    assert submit(world).status_code == 409


def test_the_live_ebook_can_be_taken_down(world):
    saved(world)
    submit(world)
    decide(world)

    down = world.as_user(world.people.author).post(world.base + "/unpublish").json()

    assert down["published_version"] is None and down["published_at"] is None and down["status"] == "approved" and "publish" in down["actions"]
    assert world.as_user(world.people.author).post(world.base + "/unpublish").status_code == 409
    assert world.as_user(world.people.author).post(world.base + "/publish").json()["published_version"] == 1             # approved content can go live again


# --- who may do what --------------------------------------------------------------------------------------------------------------------------------------

def test_the_owning_business_edits_and_a_provider_of_it_too(world):
    assert save(world, user=world.people.author).status_code == 200
    assert save(world, user=world.people.provider).status_code == 200


def test_others_cannot_author(world):
    saved(world)
    for user, code in ((world.people.outsider, 403), (world.people.learner, 403), (world.people.stranger, 403)):
        assert world.as_user(user).get(world.base).status_code == code, user["name"]
        assert world.as_user(user).put(world.base, json={"ebook": doc()}).status_code == code
        assert world.as_user(user).post(world.base + "/submit", json={}).status_code == code
        assert world.as_user(user).post(world.base + "/validate", json={}).status_code == code
        assert world.as_user(user).get(world.base + "/history").status_code == code
    assert world.as_user(None).get(world.base).status_code == 401


def test_a_super_admin_reviews_but_does_not_author(world):
    saved(world)
    root = world.people.root

    assert world.as_user(root).get(world.base).status_code == 200
    assert world.as_user(root).put(world.base, json={"ebook": doc()}).status_code == 403
    assert world.as_user(root).post(world.base + "/submit", json={}).status_code == 403
    assert world.as_user(root).post(world.base + "/withdraw").status_code == 403
    assert world.as_user(root).patch(world.base + "/pages/reorder", json={"page_ids": ["page_1", "page_2"]}).status_code == 403


def test_only_a_super_admin_decides(world):
    saved(world)
    submit(world)

    for user in (world.people.author, world.people.provider, world.people.outsider, world.people.learner):
        assert decide(world, user=user).status_code == 403, user["name"]
        assert world.as_user(user).get(world.admin_base).status_code == 403
        assert world.as_user(user).get("/admin/ebooks/pending").status_code == 403
    assert world.db.query(TrainingEbook).one().status == "pending_approval"


def test_the_lesson_must_exist_and_be_an_ebook_lesson(world):
    author = world.as_user(world.people.author)
    base = world.base.replace(LESSON, "les-video")

    assert author.get(base).status_code == 409 and "not an eBook lesson" in author.get(base).json()["detail"]
    assert author.get(world.base.replace(LESSON, "nope")).status_code == 404
    assert author.get(world.base.replace(SECTION, "wrong-section")).status_code == 404
    assert author.get(world.base.replace(str(world.training.id), "00000000-0000-4000-8000-000000000000")).status_code == 404


# --- history ---------------------------------------------------------------------------------------------------------------------------------------------------------

def test_every_change_is_recorded_with_who_made_it(world):
    saved(world)
    saved(world, doc([page("page_1", "Two")]), user=world.people.provider)
    submit(world)
    decide(world, "request_changes", "Fix the title")
    saved(world, doc([page("page_1", "Fixed")]))
    submit(world)
    decide(world)

    history = world.as_user(world.people.author).get(world.base + "/history").json()

    actions = [e["action"] for e in history["items"]][::-1]
    assert actions == ["created", "saved", "submitted", "changes_requested", "saved", "submitted", "approved", "published"]
    first, second = history["items"][::-1][0], history["items"][::-1][1]
    assert first["actor_name"] == "Asha Author" and first["actor_role"] == "admin" and second["actor_name"] == "Dr Provider"
    changes = next(e for e in history["items"] if e["action"] == "changes_requested")
    assert changes["note"] == "Fix the title" and changes["actor_role"] == "super_admin" and changes["from_status"] == "pending_approval"
    assert [(v["version"], v["kind"]) for v in history["versions"]][::-1] == [(2, "submitted"), (3, "submitted"), (3, "approved"), (3, "published")]
    assert world.as_user(world.people.outsider).get(world.base + "/history").status_code == 403


def test_a_kept_version_can_be_read_and_restored(world):
    saved(world, doc([page("page_1", "Version one")]))
    submit(world)
    decide(world)
    saved(world, doc([page("page_1", "Version two")]))
    author = world.as_user(world.people.author)

    old = author.get(world.base + "/versions/1", params={"kind": "published"}).json()

    assert old["kind"] == "published" and old["ebook"]["pages"][0]["title"] == "Version one"
    assert author.get(world.base + "/versions/9").status_code == 404
    assert author.get(world.base + "/versions/2").status_code == 404                           # version 2 was a plain save, which is not kept
    restored = author.post(world.base + "/versions/1/restore").json()
    assert restored["version"] == 3 and restored["status"] == "draft" and restored["ebook"]["pages"][0]["title"] == "Version one"
    assert [e["action"] for e in author.get(world.base + "/history").json()["items"]][0] == "restored"


# --- the Super Admin's view -----------------------------------------------------------------------------------------------------------------------------------

def test_the_review_view_has_everything_needed_to_decide(world):
    asset = upload(world)
    saved(world, doc([page("p1", "One", images=[image_block(asset)]), page("p2", "Two")]))
    submit(world)
    decide(world)
    saved(world, doc([page("p1", "One changed", images=[image_block(asset, width="medium")]), page("p3", "Three")]), user=world.people.provider)

    view = world.as_user(world.people.root).get(world.admin_base, params={"include_drafts": "true"}).json()

    assert view["training_title"] == "Nutrition 101" and view["lesson_title"] == "Nutrition basics" and view["status"] == "draft"
    assert view["last_editor"]["name"] == "Dr Provider" and view["last_updated"] and view["version"] == 2 and view["published_version"] == 1
    assert [p["id"] for p in view["published"]["pages"]] == ["p1", "p2"] and [p["id"] for p in view["draft"]["pages"]] == ["p1", "p3"]
    draft_image = view["draft"]["pages"][0]["images"][0]
    assert draft_image["width"] == "medium" and draft_image["alt_text"] == "Healthy food diagram" and draft_image["caption"] and draft_image["natural_width"] == 800
    assert view["validation"]["valid"] is True
    assert view["changes"] == {"pages_added": ["p3"], "pages_removed": ["p2"], "pages_changed": ["p1"], "order_changed": False, "details_changed": [], "is_first_version": False}
    assert [e["action"] for e in view["history"]][0] == "saved"


def test_the_review_view_shows_validation_errors(world):
    saved(world, doc([page("p1", "", "<p>x</p>", [image_block(upload(world), alt_text="")])]))

    view = world.as_user(world.people.root).get(world.admin_base, params={"include_drafts": "true"}).json()

    assert view["validation"]["valid"] is False
    assert sorted(e["code"] for e in view["validation"]["errors"]) == ["alt_text_required", "page_title_missing"]
    assert world.as_user(world.people.root).get(world.admin_base.replace(LESSON, "nope"), params={"include_drafts": "true"}).status_code == 404


def test_the_queue_lists_pending_ebooks_oldest_first_with_who_sent_them(world):
    saved(world)
    submit(world)

    queue = world.as_user(world.people.root).get("/admin/ebooks/pending").json()

    assert queue["total"] == 1
    [item] = queue["items"]
    assert item["training_title"] == "Nutrition 101" and item["lesson_title"] == "Nutrition basics" and item["page_count"] == 2 and item["submitted_by"]["name"] == "Asha Author"
    decide(world)
    assert world.as_user(world.people.root).get("/admin/ebooks/pending").json()["total"] == 0


# --- the older way and the learner payloads ---------------------------------------------------------------------------------------------------

def test_the_old_json_in_the_lesson_content_is_offered_as_a_starting_point(world):
    legacy = {"ebook": {"subtitle": "Old", "pages": [{"id": "page_1", "order": 1, "title": "From JSON", "content_html": "<p>old</p><script>x</script>", "images": []}]}}
    lesson = world.training.sections[0]["lessons"][0]
    lesson["content"] = json.dumps(legacy)
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(world.training, "sections")
    world.db.commit()

    body = world.as_user(world.people.author).get(world.base).json()

    assert body["imported_from_lesson_content"] is True and body["version"] == 0 and body["ebook"]["pages"][0]["title"] == "From JSON"
    assert "script" not in body["ebook"]["pages"][0]["content_html"] and body["ebook"]["subtitle"] == "Old"
    saved(world, body["ebook"])
    assert world.as_user(world.people.author).get(world.base).json()["imported_from_lesson_content"] is False


def test_the_old_json_never_reaches_a_learner_through_the_course_content(world):
    from sqlalchemy.orm.attributes import flag_modified

    lesson = world.training.sections[0]["lessons"][0]
    lesson["content"] = json.dumps({"ebook": {"pages": [{"title": "SECRET DRAFT"}]}})
    lesson["detail"] = lesson["content"]
    flag_modified(world.training, "sections")
    world.db.commit()
    tid = world.training.id

    for user in (world.people.learner,):
        content = world.as_user(user).get(f"/trainings/{tid}/content")
        assert content.status_code == 200
        assert "SECRET DRAFT" not in content.text and content.json()["sections"][0]["lessons"][0]["type"] == "ebook"
        single = world.as_user(user).get(f"/trainings/{tid}/sections/{SECTION}/lessons/{LESSON}")
        assert "SECRET DRAFT" not in single.text
    assert "SECRET DRAFT" in world.as_user(world.people.author).get(f"/trainings/{tid}/sections/{SECTION}/lessons/{LESSON}").text            # the author still sees their own


def test_the_ebook_lesson_type_is_accepted_when_building_a_course(world):
    resp = world.as_user(world.people.author).post(f"/trainings/{world.training.id}/sections/{SECTION}/lessons", json={"type": "ebook", "title": "Another eBook"})

    assert resp.status_code == 201, resp.text
    world.db.refresh(world.training)
    assert [l["type"] for l in world.training.sections[0]["lessons"]] == ["ebook", "video", "ebook"]


def test_picture_ids_are_filled_in_when_left_out(world):
    asset = upload(world)
    block = {k: val for k, val in image_block(asset).items() if k != "id"}
    saved(world, doc([page("p1", "One", images=[block, dict(block)])]))

    images = world.as_user(world.people.author).get(world.base).json()["ebook"]["pages"][0]["images"]

    assert len({i["id"] for i in images}) == 2 and all(i["id"].startswith("image_") for i in images)


def test_what_the_server_returns_can_be_sent_straight_back(world):
    asset = upload(world)
    saved(world, doc([page("p1", "One", "<p>ok</p><script>x</script>", [image_block(asset)])], cover_asset_id=asset))
    loaded = world.as_user(world.people.author).get(world.base).json()

    again = save(world, loaded["ebook"], base_version=loaded["version"])

    assert again.status_code == 200 and again.json()["version"] == 2
    reloaded = world.as_user(world.people.author).get(world.base).json()["ebook"]
    assert reloaded["pages"][0]["images"][0]["asset_id"] == asset and reloaded["cover_asset_id"] == asset


def test_a_duplicated_training_keeps_its_ebook_as_a_draft(world):
    from tests.ebook_support import go_live

    go_live(world, doc([page("p1", "Original")]))

    clone = world.as_user(world.people.author).post(f"/trainings/{world.training.id}/duplicate")

    assert clone.status_code == 201, clone.text
    copy = world.db.query(TrainingEbook).filter(TrainingEbook.training_id != world.training.id).one()
    assert copy.status == "draft" and copy.published is None and copy.draft["pages"][0]["title"] == "Original" and copy.draft_version == 1
    assert world.db.query(TrainingEbook).filter(TrainingEbook.training_id == world.training.id).one().published is not None      # the original is untouched


def test_deleting_a_lesson_releases_its_pictures(world):
    from sqlalchemy.orm.attributes import flag_modified

    asset = upload(world)
    saved(world, doc([page("p1", "One", images=[image_block(asset)])]))
    assert world.as_user(world.people.author).delete(f"/media/{asset}").status_code == 409
    world.training.sections[0]["lessons"] = [l for l in world.training.sections[0]["lessons"] if l["id"] != LESSON]
    flag_modified(world.training, "sections")
    world.db.commit()

    assert world.as_user(world.people.author).delete(f"/media/{asset}").status_code == 200
