"""The Blog form builder on the shared form model: field and section operations, protected fields, audit history, review and submit, the three
audience views, every field type through a real post, and older stored definitions."""
import pytest

from app.core.config import settings
from app.models.blog_model import BlogForm, BlogFormVersion
from tests.blog_support import API, BODY, draft, live_merchant_post, make_category, review, submit, world  # noqa: F401
from tests.test_blog_forms_and_posts import make_form, png, publish_form, root, upload, uploads_dir  # noqa: F401

SMALL = {"sections": [{"key": "basics", "title": "Basics", "fields": [{"key": "title"}, {"key": "category"}, {"key": "content"}, {"key": "excerpt"}]}]}
BASE = f"{API}/admin/forms"


def new_form(w, **over):
    return make_form(w, name=over.pop("name", "Builder form"), definition=over.pop("definition", SMALL), **over)


def fields_of(form):
    return {f["key"]: f for s in form["definition"]["sections"] for f in s["fields"]}


def section_id(form, key="basics"):
    return next(s["id"] for s in form["definition"]["sections"] if s["key"] == key)


def add(w, form, field, **extra):
    body = {"section_id": extra.pop("section_id", section_id(form)), "field": field, **extra}
    return root(w).post(f"{BASE}/{form['id']}/fields", json=body)


# --- the catalogue ----------------------------------------------------------------------------------------------------------

def test_the_builder_can_ask_which_field_types_exist(world):
    types = root(world).get(f"{API}/admin/form-field-types").json()
    assert [t["type"] for t in types] == ["short_text", "rich_text", "long_text", "dropdown", "radio", "checkbox", "number", "date", "time", "reference_url",
                                          "additional_images", "supporting_document", "video", "tags"]
    by = {t["type"]: t for t in types}
    assert by["dropdown"]["supports_options"] is True and by["short_text"]["supports_options"] is False and by["video"]["renderer"] == "video_input"
    assert by["number"]["rules"]["decimals"] == {"kind": "int", "min": 0, "max": 6} and "png" in by["additional_images"]["accepts_files"] and "mp4" in by["video"]["accepts_files"]
    assert by["short_text"]["rules"]["format"] == {"kind": "enum", "values": ["email", "phone"]}
    assert world.as_user(world.people.asha).get(f"{API}/admin/form-field-types").status_code == 403


# --- fields and sections ----------------------------------------------------------------------------------------------------

def test_fields_are_added_edited_duplicated_moved_reordered_and_deleted_through_the_api(world):
    form = new_form(world)
    first = form["revision"]
    sec = section_id(form)

    extra = root(world).post(f"{BASE}/{form['id']}/sections", json={"title": "Details", "description": "More", "base_revision": first})
    assert extra.status_code == 201 and extra.json()["revision"] == first + 1 and [s["title"] for s in extra.json()["definition"]["sections"]] == ["Basics", "Details"]
    assert extra.json()["change"]["action"] == "section_added"
    details = section_id(extra.json(), "details")

    added = add(world, extra.json(), {"key": "speaker", "label": "Speaker", "type": "short_text", "required": True, "help_text": "Full name", "placeholder": "Dr Rao",
                                      "validation": {"min_length": 2, "max_length": 60}}, section_id=details, position=1)
    body = added.json()
    assert added.status_code == 201 and body["change"]["action"] == "field_added" and body["change"]["key"] == "speaker" and body["revision"] == first + 2
    speaker = fields_of(body)["speaker"]
    assert speaker["id"] == body["change"]["field_id"] and speaker["renderer"] == "text_input" and speaker["order"] == 1 and speaker["system"] is False and speaker["visibility"] == "published"
    assert speaker["required"] is True and speaker["enabled"] is True and speaker["validation"] == {"min_length": 2, "max_length": 60} and speaker["kind"] == "custom"

    edited = root(world).patch(f"{BASE}/{form['id']}/fields/{speaker['id']}", json={"patch": {"label": "Main speaker", "enabled": False}})
    assert edited.status_code == 200 and fields_of(edited.json())["speaker"]["label"] == "Main speaker" and fields_of(edited.json())["speaker"]["required"] is False
    assert root(world).patch(f"{BASE}/{form['id']}/fields/speaker", json={"patch": {"enabled": True, "required": True}}).status_code == 200      # by key too
    for patch in ({"key": "renamed"}, {"type": "number"}):
        bad = root(world).patch(f"{BASE}/{form['id']}/fields/speaker", json={"patch": patch})
        assert bad.status_code == 422 and bad.json()["errors"][0]["code"] in ("KEY_IMMUTABLE", "TYPE_IMMUTABLE")

    copy = root(world).post(f"{BASE}/{form['id']}/fields/speaker/duplicate")
    clone = fields_of(copy.json())["speaker_copy"]
    assert copy.status_code == 201 and clone["label"] == "Main speaker (copy)" and clone["id"] != speaker["id"] and copy.json()["change"]["copied_from"] == "speaker"

    moved = root(world).post(f"{BASE}/{form['id']}/fields/speaker_copy/move", json={"section_id": sec, "position": 1})
    assert moved.status_code == 200 and [f["key"] for f in moved.json()["definition"]["sections"][0]["fields"]][0] == "speaker_copy"
    ids = [f["id"] for f in moved.json()["definition"]["sections"][0]["fields"]]
    ordered = root(world).put(f"{BASE}/{form['id']}/sections/{sec}/fields/order", json={"ids": list(reversed(ids))})
    assert ordered.status_code == 200 and [f["id"] for f in ordered.json()["definition"]["sections"][0]["fields"]] == list(reversed(ids))
    assert root(world).put(f"{BASE}/{form['id']}/sections/{sec}/fields/order", json={"ids": ids[:1]}).json()["code"] == "INVALID_ORDER"

    sections = [s["id"] for s in ordered.json()["definition"]["sections"]]
    swapped = root(world).put(f"{BASE}/{form['id']}/sections/order", json={"ids": list(reversed(sections))})
    assert [s["title"] for s in swapped.json()["definition"]["sections"]] == ["Details", "Basics"] and [s["order"] for s in swapped.json()["definition"]["sections"]] == [1, 2]
    renamed = root(world).patch(f"{BASE}/{form['id']}/sections/{details}", json={"title": "More details"})
    assert renamed.json()["definition"]["sections"][0]["title"] == "More details"

    gone = root(world).delete(f"{BASE}/{form['id']}/fields/speaker_copy")
    assert gone.status_code == 200 and "speaker_copy" not in fields_of(gone.json())
    full = root(world).delete(f"{BASE}/{form['id']}/sections/{details}")
    assert full.status_code == 409 and full.json()["code"] == "SECTION_NOT_EMPTY"
    done = root(world).delete(f"{BASE}/{form['id']}/sections/{details}", params={"move_to": sec})
    assert done.status_code == 200 and [s["key"] for s in done.json()["definition"]["sections"]] == ["basics"] and "speaker" in fields_of(done.json())


def test_duplicates_core_fields_and_protected_fields_are_refused(world):
    form = new_form(world)
    for field, code in (({"key": "excerpt", "label": "Another", "type": "long_text"}, "KEY_RESERVED"), ({"key": "title", "label": "Another", "type": "short_text"}, "KEY_RESERVED"),
                        ({"key": "summary_x", "label": "Summary", "type": "short_text"}, "DUPLICATE_LABEL")):
        resp = add(world, form, field)
        assert resp.status_code in (409, 422) and resp.json()["code"] == code
    ok = add(world, form, {"key": "audience", "label": "Audience", "type": "radio", "options": [{"value": "a"}, {"value": "b"}]})
    assert ok.status_code == 201
    dup_key = add(world, ok.json(), {"key": "audience", "label": "Audience 2", "type": "short_text"})
    assert dup_key.status_code == 409 and dup_key.json()["code"] == "DUPLICATE_KEY"
    dup_option = add(world, ok.json(), {"key": "mood", "label": "Mood", "type": "dropdown", "options": [{"value": "x", "label": "Same"}, {"value": "y", "label": "same"}]})
    assert dup_option.status_code == 422 and dup_option.json()["errors"][0]["code"] == "DUPLICATE_OPTION"

    for call in (lambda: root(world).delete(f"{BASE}/{form['id']}/fields/title"), lambda: root(world).patch(f"{BASE}/{form['id']}/fields/title", json={"patch": {"enabled": False}}),
                 lambda: root(world).patch(f"{BASE}/{form['id']}/fields/content", json={"patch": {"required": False}}),
                 lambda: root(world).patch(f"{BASE}/{form['id']}/fields/category", json={"patch": {"visibility": "enterprise_admin_only"}})):
        refused = call()
        assert refused.status_code == 409 and refused.json()["code"] == "PROTECTED_FIELD"
    core = root(world).delete(f"{BASE}/{form['id']}/fields/excerpt")
    assert core.status_code == 409 and core.json()["code"] == "CORE_FIELD"
    assert root(world).patch(f"{BASE}/{form['id']}/fields/excerpt", json={"patch": {"enabled": False}}).status_code == 200        # switch off instead
    renamed = root(world).patch(f"{BASE}/{form['id']}/fields/title", json={"patch": {"label": "Headline", "help_text": "Short"}})
    assert renamed.status_code == 200 and fields_of(renamed.json())["title"]["label"] == "Headline" and fields_of(renamed.json())["title"]["system"] is True
    lonely = root(world).delete(f"{BASE}/{form['id']}/sections/{section_id(form)}")
    assert lonely.json()["code"] == "LAST_SECTION"


def test_a_standard_form_cannot_be_built_on_and_a_stale_revision_is_refused(world):
    standard = make_form(world, name="Plain", mode="standard", definition=None)
    resp = root(world).post(f"{BASE}/{standard['id']}/sections", json={"title": "Extra"})
    assert resp.status_code == 409 and resp.json()["code"] == "STANDARD_FORM_FIXED"

    form = new_form(world)
    assert root(world).post(f"{BASE}/{form['id']}/sections", json={"title": "One", "base_revision": form["revision"]}).status_code == 201
    stale = root(world).post(f"{BASE}/{form['id']}/sections", json={"title": "Two", "base_revision": form["revision"]})
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT" and stale.json()["current_version"] == form["revision"] + 1
    assert root(world).post(f"{BASE}/{form['id']}/fields", json={"section_id": "nope", "field": {"key": "ab", "label": "A", "type": "short_text"}}).json()["code"] == "SECTION_NOT_FOUND"
    assert root(world).post(f"{BASE}/{form['id']}/fields", json={"section_id": section_id(form), "field": {"key": "ab", "label": "A", "type": "colour"}}).json()["errors"][0]["code"] == "INVALID_TYPE"
    for user in (world.people.asha, world.people.priya):
        assert world.as_user(user).post(f"{BASE}/{form['id']}/sections", json={"title": "X"}).status_code == 403
    assert world.as_user(None).post(f"{BASE}/{form['id']}/sections", json={"title": "X"}).status_code == 401


# --- audit, review, submit -----------------------------------------------------------------------------------------------------

def test_every_change_is_in_the_audit_history_which_outlives_the_form(world):
    form = new_form(world)
    add(world, form, {"key": "speaker", "label": "Speaker", "type": "short_text"})
    root(world).patch(f"{BASE}/{form['id']}/fields/speaker", json={"patch": {"label": "Main speaker"}})
    root(world).post(f"{BASE}/{form['id']}/fields/speaker/duplicate")
    publish_form(world, form, note="First version")
    root(world).post(f"{BASE}/{form['id']}/deactivate")
    root(world).post(f"{BASE}/{form['id']}/activate")

    audit = root(world).get(f"{BASE}/{form['id']}/audit").json()
    actions = [i["action"] for i in audit["items"]]
    assert actions == ["activated", "deactivated", "published", "field_duplicated", "field_edited", "field_added", "created"] and audit["pagination"]["total"] == 7
    newest = audit["items"][0]
    assert newest["actor_id"] == world.people.root["id"] and newest["actor_name"] == "Platform Root" and newest["actor_role"] == "super_admin" and newest["created_at"]
    published = audit["items"][2]
    assert published["version"] == 1 and "First version" in published["summary"] and audit["items"][4]["detail"]["key"] == "speaker"
    assert [i["action"] for i in root(world).get(f"{BASE}/{form['id']}/audit", params={"action": "published"}).json()["items"]] == ["published"]
    assert world.as_user(world.people.asha).get(f"{BASE}/{form['id']}/audit").status_code == 403

    spare = new_form(world, name="Spare")
    assert root(world).delete(f"{BASE}/{spare['id']}").status_code == 200
    assert root(world).get(f"{BASE}/{spare['id']}").status_code == 404                  # the form itself is gone ...
    kept = root(world).get(f"{BASE}/{spare['id']}/audit").json()
    assert [i["action"] for i in kept["items"]] == ["deleted", "created"]                # ... its audit history is not


def test_review_lists_the_problems_and_changes_and_submit_is_a_checkpoint(world):
    form = new_form(world)
    first = root(world).get(f"{BASE}/{form['id']}/review").json()
    assert first["ready"] is True and first["problems"] == [] and first["changes"]["since_version"] is None and "title" in first["changes"]["added"]
    assert first["review_status"] == "draft" and first["review_required_to_publish"] is False

    publish_form(world, form)
    add(world, form, {"key": "speaker", "label": "Speaker", "type": "short_text"})
    root(world).patch(f"{BASE}/{form['id']}/fields/excerpt", json={"patch": {"help_text": "One line"}})
    changes = root(world).get(f"{BASE}/{form['id']}/review").json()["changes"]
    assert changes["since_version"] == 1 and changes["added"] == ["speaker"] and changes["removed"] == [] and changes["changed"] == [{"key": "excerpt", "label": "Summary", "changed": ["help_text"]}]

    submitted = root(world).post(f"{BASE}/{form['id']}/submit", json={})
    assert submitted.status_code == 200 and submitted.json()["review_status"] == "submitted"
    root(world).patch(f"{BASE}/{form['id']}/fields/speaker", json={"patch": {"label": "Speaker name"}})
    assert root(world).get(f"{BASE}/{form['id']}/review").json()["review_status"] == "draft"          # an edit withdraws it
    broken = root(world).get(f"{BASE}/{new_form(world, name='Broken', scope='selected', tenant_ids=[str(world.people.asha['tenant_id'])])['id']}/review")
    assert broken.status_code == 200

    settings.BLOG_FORM_REQUIRE_REVIEW = True
    try:
        refused = root(world).post(f"{BASE}/{form['id']}/publish", json={})
        assert refused.status_code == 409 and refused.json()["code"] == "REVIEW_REQUIRED"
        assert root(world).post(f"{BASE}/{form['id']}/submit", json={}).status_code == 200
        assert root(world).post(f"{BASE}/{form['id']}/publish", json={}).json()["current_version"] == 2
        assert root(world).get(f"{BASE}/{form['id']}").json()["review_status"] == "draft"             # publishing used it up
    finally:
        settings.BLOG_FORM_REQUIRE_REVIEW = False
    assert "submitted" in [i["action"] for i in root(world).get(f"{BASE}/{form['id']}/audit").json()["items"]]


def test_a_draft_with_problems_cannot_be_submitted(world):
    form = new_form(world)
    bad = {"sections": [{"key": "only", "title": "Only", "fields": [{"key": "title"}, {"key": "content"}]}]}
    row = world.db.get(BlogForm, __import__("uuid").UUID(form["id"]))
    row.draft_definition = bad                                                               # a hand-edited draft that lost the category field
    world.db.commit()
    review_ = root(world).get(f"{BASE}/{form['id']}/review").json()
    assert review_["ready"] is False and any(p["code"] == "MISSING_CORE_FIELD" for p in review_["problems"])
    refused = root(world).post(f"{BASE}/{form['id']}/submit", json={})
    assert refused.status_code == 422 and refused.json()["code"] == "FORM_NOT_READY" and refused.json()["errors"]


# --- the three views --------------------------------------------------------------------------------------------------------

VIEWS = {"sections": [
    {"key": "basics", "title": "Basics", "fields": [{"key": "title"}, {"key": "category"}, {"key": "content"}, {"key": "excerpt", "enabled": False}]},
    {"key": "extra", "title": "Extra", "fields": [
        {"key": "speaker", "label": "Speaker", "type": "short_text", "help_text": "Full name", "placeholder": "Dr Rao", "validation": {"max_length": 60}, "required": True},
        {"key": "editor_note", "label": "Editor note", "type": "long_text", "visibility": "enterprise_admin_only", "help_text": "Private"},
        {"key": "teaser", "label": "Teaser", "type": "video", "enabled": False},
        {"key": "agenda", "label": "Agenda", "type": "supporting_document", "visibility": "enterprise_admin_only"},
        {"key": "audience", "label": "Audience", "type": "radio", "options": [{"value": "kids", "label": "Kids"}, {"value": "adults", "label": "Adults"}]}]}]}


def test_public_enterprise_and_super_admin_each_get_their_own_view_of_the_form(world):
    publish_form(world, new_form(world, definition=VIEWS))

    public = world.as_user(None).get(f"{API}/forms/public", params={"kind": "blog"}).json()
    public_fields = {f["key"]: f for s in public["definition"]["sections"] for f in s["fields"]}
    assert set(public_fields) == {"title", "category", "content", "speaker", "audience"}                  # no editor note, agenda, teaser or switched-off excerpt
    assert set(public_fields["speaker"]) == {"id", "key", "label", "type", "renderer", "order", "options"} and public_fields["audience"]["type"] == "radio"
    assert public_fields["audience"]["options"] == [{"value": "kids", "label": "Kids"}, {"value": "adults", "label": "Adults"}]
    assert "Private" not in str(public) and "Dr Rao" not in str(public) and "enterprise_admin_only" not in str(public)

    mine = world.as_user(world.people.asha).get(f"{API}/my/form", params={"kind": "blog"}).json()
    mine_fields = {f["key"]: f for s in mine["definition"]["sections"] for f in s["fields"]}
    assert set(mine_fields) == {"title", "category", "content", "speaker", "editor_note", "agenda", "audience"}      # Enterprise Admin only fields are there, disabled ones are not
    assert mine_fields["speaker"]["placeholder"] == "Dr Rao" and mine_fields["speaker"]["required"] is True and mine_fields["speaker"]["validation"] == {"max_length": 60}
    assert mine_fields["editor_note"]["visibility"] == "enterprise_admin_only" and mine_fields["title"]["protected"] is True and "system" not in mine_fields["title"]
    assert mine_fields["category"]["options_source"] == "blog_categories"

    form_id = root(world).get(BASE).json()["items"][0]["id"]
    builder = root(world).get(f"{BASE}/{form_id}").json()
    builder_fields = fields_of(builder)
    assert set(builder_fields) >= {"excerpt", "teaser", "editor_note"} and builder_fields["excerpt"]["enabled"] is False and builder_fields["title"]["system"] is True
    active = root(world).get(f"{BASE}/active").json()
    assert "teaser" in str(active["blog"]["definition"])                                              # the Super Admin's view of what a business gets

    preview = root(world).get(f"{BASE}/{form_id}/preview").json()
    assert "teaser" in str(preview["definition"]) and "teaser" not in str(preview["enterprise_admin_view"]) and "editor_note" not in str(preview["public_view"])


# --- every field type through a real post ---------------------------------------------------------------------------------------

ALL_TYPES = {"sections": [
    {"key": "basics", "title": "Basics", "fields": [{"key": "title"}, {"key": "category"}, {"key": "content"}, {"key": "excerpt"}]},
    {"key": "all", "title": "All types", "fields": [
        {"key": "f_short", "label": "Short", "type": "short_text", "validation": {"max_length": 20}},
        {"key": "f_rich", "label": "Rich", "type": "rich_text"},
        {"key": "f_long", "label": "Long", "type": "long_text", "validation": {"min_length": 5}},
        {"key": "f_drop", "label": "Drop", "type": "dropdown", "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]},
        {"key": "f_radio", "label": "Radio", "type": "radio", "options": [{"value": "yes"}, {"value": "no"}]},
        {"key": "f_check", "label": "Check", "type": "checkbox", "options": [{"value": "x"}, {"value": "y"}, {"value": "z"}], "validation": {"max_items": 2}},
        {"key": "f_num", "label": "Number", "type": "number", "validation": {"min": 0, "max": 100, "decimals": 1}},
        {"key": "f_date", "label": "Date", "type": "date", "validation": {"min": "2026-01-01"}},
        {"key": "f_time", "label": "Time", "type": "time", "validation": {"min": "08:00", "max": "20:00"}},
        {"key": "f_url", "label": "Link", "type": "reference_url"},
        {"key": "f_video", "label": "Video", "type": "video", "validation": {"max_items": 2}},
        {"key": "f_images", "label": "Images", "type": "additional_images", "validation": {"max_items": 2}},
        {"key": "f_docs", "label": "Docs", "type": "supporting_document"},
        {"key": "f_tags", "label": "Tags", "type": "tags", "validation": {"max_items": 3}},
        {"key": "f_secret", "label": "Secret", "type": "short_text", "visibility": "enterprise_admin_only"}]}]}


def test_every_field_type_is_saved_checked_and_shown_with_its_explicit_type(world, uploads_dir):
    publish_form(world, new_form(world, definition=ALL_TYPES))
    cat = make_category(world)
    asha = world.people.asha
    image = upload(world, asha, "a.png", png(), "image/png", "image").json()
    pdf = upload(world, asha, "r.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf", "document").json()
    values = {"f_short": "Hello", "f_rich": "<p>Some <b>rich</b> text</p><script>x()</script>", "f_long": "A long enough text", "f_drop": "b", "f_radio": "yes", "f_check": ["x", "z"],
              "f_num": 12.5, "f_date": "2026-06-01", "f_time": "09:30", "f_url": "https://youtu.be/dQw4w9WgXcQ?t=5",
              "f_video": ["https://vimeo.com/76979871", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"], "f_images": [image["url"]], "f_docs": [pdf["url"]],
              "f_tags": ["Wellness", "wellness", "Tips"], "f_secret": "Do not show"}
    post = draft(world, asha, cat, custom_fields=values)
    saved = post["custom_fields"]
    assert saved["f_rich"] == "<p>Some <b>rich</b> text</p>" and saved["f_num"] == 12.5 and saved["f_time"] == "09:30" and saved["f_tags"] == ["Wellness", "Tips"]
    assert saved["f_url"] == {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "provider": "youtube", "video_id": "dQw4w9WgXcQ", "thumbnail_url": "https://img.youtube.com/vi/dQw4w9WgXcQ/hqdefault.jpg"}
    assert [v["provider"] for v in saved["f_video"]] == ["vimeo", "youtube"] and saved["f_docs"][0]["name"] == "r.pdf" and saved["f_docs"][0]["content_type"] == "application/pdf"
    assert saved["f_images"][0]["url"] == image["url"]

    submit(world, asha, post)
    live = review(world, post, "approve").json()
    public = world.as_user(None).get(f"{API}/{live['slug']}").json()
    by_key = {f["key"]: f for f in public["custom_fields"]}
    assert {k: (f["type"], f["renderer"]) for k, f in by_key.items()} == {
        "f_short": ("short_text", "text_input"), "f_rich": ("rich_text", "rich_text_editor"), "f_long": ("long_text", "textarea"), "f_drop": ("dropdown", "select"),
        "f_radio": ("radio", "radio_group"), "f_check": ("checkbox", "checkbox_group"), "f_num": ("number", "number_input"), "f_date": ("date", "date_picker"),
        "f_time": ("time", "time_picker"), "f_url": ("reference_url", "url_input"), "f_video": ("video", "video_input"), "f_images": ("additional_images", "image_uploader"),
        "f_docs": ("supporting_document", "document_uploader"), "f_tags": ("tags", "tag_input")}                         # f_secret is not there
    assert "Do not show" not in str(public) and "file_id" not in str(public)
    assert by_key["f_url"]["value"]["video_id"] == "dQw4w9WgXcQ" and by_key["f_video"]["value"][0]["video_id"] == "76979871"

    mine = world.as_user(asha).get(f"{API}/my/posts/{post['id']}").json()
    assert mine["custom_fields"]["f_secret"] == "Do not show"                                                              # the Enterprise Admin still sees and edits it
    assert world.as_user(world.people.root).get(f"{API}/admin/posts/{post['id']}").json()["custom_fields"]["f_secret"] == "Do not show"


@pytest.mark.parametrize("key, value, code", [
    ("f_short", "x" * 21, "TOO_LONG"), ("f_long", "abc", "TOO_SHORT"), ("f_drop", "z", "INVALID_OPTION"), ("f_radio", "maybe", "INVALID_OPTION"),
    ("f_check", ["x", "y", "z"], "TOO_MANY"), ("f_num", 1.25, "TOO_MANY_DECIMALS"), ("f_num", 101, "TOO_LARGE"), ("f_date", "2025-12-31", "TOO_SMALL"), ("f_time", "07:00", "TOO_SMALL"),
    ("f_time", "9am", "INVALID_FORMAT"), ("f_url", "javascript:alert(1)", "UNSAFE_SCHEME"), ("f_url", "http://169.254.169.254/latest", "PRIVATE_HOST"),
    ("f_url", "https://user:pw@example.com", "CREDENTIALS"), ("f_video", ["https://www.youtube.com/watch?v=bad"], "INVALID_VIDEO"), ("f_video", ["https://a.test/1", "https://a.test/2", "https://a.test/3"], "TOO_MANY"),
    ("f_images", ["https://a.test/1.png", "https://a.test/2.png", "https://a.test/3.png"], "TOO_MANY"), ("f_images", ["https://a.test/x.exe"], "FILE_TYPE_NOT_ALLOWED"),
    ("f_docs", [{"url": "https://a.test/f.pdf", "size": 60 * 1024 * 1024}], "FILE_TOO_LARGE"), ("f_tags", ["a", "b", "c", "d"], "TOO_MANY"), ("f_tags", "one", "INVALID_TYPE"),
])
def test_each_rule_is_enforced_with_its_own_error_code(world, key, value, code):
    publish_form(world, new_form(world, definition=ALL_TYPES))
    cat = make_category(world)
    post = draft(world, world.people.asha, cat)
    resp = world.as_user(world.people.asha).patch(f"{API}/my/posts/{post['id']}", json={"custom_fields": {key: value}})
    assert resp.status_code == 422 and resp.json()["errors"][0]["code"] == code and resp.json()["errors"][0]["field"].startswith(f"custom_fields.{key}")


def test_enterprise_admin_only_values_never_reach_the_public_but_platform_posts_cannot_carry_them(world):
    publish_form(world, new_form(world, definition=ALL_TYPES))
    cat = make_category(world)
    platform = root(world).post(f"{API}/admin/posts", json={"title": "Platform", "content": BODY, "category_id": cat["id"], "custom_fields": {"f_secret": "x"}})
    assert platform.status_code == 422 and platform.json()["errors"][0]["code"] == "UNKNOWN_FIELD"
    fine = root(world).post(f"{API}/admin/posts", json={"title": "Platform two", "content": BODY, "category_id": cat["id"], "status": "published", "custom_fields": {"f_short": "ok"}})
    assert fine.status_code == 201
    assert "f_secret" not in str(world.as_user(None).get(f"{API}/{fine.json()['slug']}").json())


# --- older definitions ---------------------------------------------------------------------------------------------------------

def test_an_older_stored_form_is_read_in_the_new_format_and_the_migration_logic_is_repeatable(world):
    legacy = {"sections": [{"key": "basics", "title": "Basics", "fields": [
        {"key": "title", "kind": "core", "type": "text", "label": "Title", "required": True, "visible": True, "visibility": "public", "enterprise_only": False, "options": [], "validation": {}},
        {"key": "category", "kind": "core", "type": "category", "label": "Category", "required": True, "visible": True, "visibility": "public", "enterprise_only": False, "options": [], "validation": {}},
        {"key": "content", "kind": "core", "type": "richtext", "label": "Body", "required": True, "visible": True, "visibility": "public", "enterprise_only": False, "options": [], "validation": {}},
        {"key": "when", "kind": "custom", "type": "date", "label": "When", "required": False, "visible": True, "visibility": "public", "enterprise_only": False, "options": [], "validation": {}},
        {"key": "pick", "kind": "custom", "type": "multiselect", "label": "Pick", "required": False, "visible": True, "visibility": "internal", "enterprise_only": False,
         "options": [{"value": "a", "label": "A"}], "validation": {}},
        {"key": "ok", "kind": "custom", "type": "checkbox", "label": "OK?", "required": False, "visible": False, "visibility": "public", "enterprise_only": True, "options": [], "validation": {}},
        {"key": "pic", "kind": "custom", "type": "image", "label": "Pic", "required": False, "visible": True, "visibility": "public", "enterprise_only": False, "options": [], "validation": {}}]}]}
    form = new_form(world)
    row = world.db.get(BlogForm, __import__("uuid").UUID(form["id"]))
    row.draft_definition, row.current_version, row.status = legacy, 1, "published"
    world.db.add(BlogFormVersion(form_id=row.id, version=1, name=row.name, form_type="both", mode="configurable", definition=legacy))
    row.is_active = True
    world.db.commit()

    from app.services.blog_form_rules import is_current_format, upgrade_definition
    assert is_current_format(legacy) is False
    mine = world.as_user(world.people.asha).get(f"{API}/my/form", params={"kind": "blog"}).json()
    got = {f["key"]: f for s in mine["definition"]["sections"] for f in s["fields"]}
    assert (got["title"]["type"], got["content"]["type"], got["category"]["type"], got["when"]["type"]) == ("short_text", "rich_text", "dropdown", "date")
    assert got["pick"]["type"] == "checkbox" and got["pick"]["visibility"] == "enterprise_admin_only" and got["pic"]["type"] == "additional_images" and got["pic"]["validation"] == {"max_items": 1}
    assert "ok" not in got                                                                                  # it was switched off (visible: false)
    first = upgrade_definition(legacy)
    assert upgrade_definition(first) == first and upgrade_definition(legacy) == first and is_current_format(first)       # the same result every time: ids come from keys
    assert {f["key"]: f["type"] for s in first["sections"] for f in s["fields"]}["ok"] == "radio"          # the old yes/no box became a yes/no radio
    assert upgrade_definition(None) is None and upgrade_definition({"sections": [{"fields": [{"type": "nope"}]}]}) == {"sections": [{"fields": [{"type": "nope"}]}]}   # unconvertible: left as it is
    detail = root(world).get(f"{BASE}/{form['id']}").json()
    assert is_current_format(detail["definition"])
    assert root(world).get(f"{BASE}/{form['id']}/versions/1").json()["definition"]["sections"][0]["fields"][0]["type"] == "short_text"
