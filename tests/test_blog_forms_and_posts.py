"""Blog / news forms (versions, assignment, the form a post is written with), the extra post fields, previews, revisions,
concurrent edits, duplicate titles and slugs, uploads, and the notification details."""
import io

import pytest
from PIL import Image

from app.core.config import settings
from tests.blog_support import (  # noqa: F401
    API, BODY, TENANT_A, TENANT_B, draft, live_merchant_post, make_category, notices_of, review, submit, world,
)

DEFINITION = {"sections": [
    {"key": "basics", "title": "Basics", "fields": [
        {"key": "title", "help_text": "Keep it short", "validation": {"max_length": 80}}, {"key": "category"}, {"key": "excerpt", "required": True}]},
    {"key": "content", "title": "Content", "fields": [{"key": "content"}]},
    {"key": "details", "title": "Event details", "fields": [
        {"key": "event_date", "type": "date", "label": "Event date", "required": True},
        {"key": "audience", "type": "select", "label": "Audience", "options": [{"value": "kids", "label": "Kids"}, {"value": "adults", "label": "Adults"}]},
        {"key": "budget", "type": "number", "label": "Budget", "validation": {"min": 0, "max": 1000, "integer": True}},
        {"key": "editor_note", "type": "textarea", "label": "Note for editors", "visibility": "internal"},
        {"key": "partner", "type": "text", "label": "Partner", "enterprise_only": True}]},
    {"key": "media", "title": "Images", "fields": [{"key": "cover_image"}, {"key": "additional_images"}, {"key": "supporting_documents", "visibility": "internal"}]},
]}


def root(w):
    return w.as_user(w.people.root)


def make_form(w, **over):
    body = {"name": "Event posts", "form_type": "both", "mode": "configurable", "scope": "global", "definition": DEFINITION, **over}
    resp = root(w).post(f"{API}/admin/forms", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def publish_form(w, form, **body):
    resp = root(w).post(f"{API}/admin/forms/{form['id']}/publish", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def my_form(w, user=None, kind="blog"):
    resp = w.as_user(user or w.people.asha).get(f"{API}/my/form", params={"kind": kind})
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- forms: building, publishing, versions ----------------------------------------------------------------------------------

def test_a_form_is_a_draft_until_it_is_published_and_then_businesses_get_it(world):
    assert my_form(world)["version"] == 0 and my_form(world)["form_id"] is None          # the built-in Standard form

    form = make_form(world)
    assert form["status"] == "draft" and form["current_version"] == 0 and form["is_active"] is False
    assert my_form(world)["form_id"] is None                                              # a draft is not offered

    done = publish_form(world, form, note="First version")
    assert done["status"] == "published" and done["current_version"] == 1 and done["is_active"] is True and done["has_unpublished_changes"] is False
    mine = my_form(world)
    assert mine["form_id"] == form["id"] and mine["version"] == 1 and mine["mode"] == "configurable"
    keys = [f["key"] for s in mine["definition"]["sections"] for f in s["fields"]]
    assert keys[:3] == ["title", "category", "excerpt"] and "event_date" in keys
    title = mine["definition"]["sections"][0]["fields"][0]
    assert title["required"] is True and title["help_text"] == "Keep it short" and title["validation"] == {"max_length": 80}


def test_a_standard_form_uses_the_built_in_fields_and_cannot_be_changed(world):
    form = make_form(world, name="Plain", mode="standard", definition=None)
    keys = [f["key"] for s in form["definition"]["sections"] for f in s["fields"]]
    assert "title" in keys and "cover_image" in keys and "event_date" not in keys

    refused = root(world).patch(f"{API}/admin/forms/{form['id']}", json={"definition": DEFINITION})
    assert refused.status_code == 422 and refused.json()["code"] == "STANDARD_FORM_FIXED"
    assert root(world).patch(f"{API}/admin/forms/{form['id']}", json={"mode": "configurable", "definition": DEFINITION}).status_code == 200


def test_a_bad_definition_lists_every_problem(world):
    bad = {"sections": [{"title": "Only", "fields": [
        {"key": "title", "visible": False}, {"key": "content"}, {"key": "Bad Key", "type": "text", "label": "x"},
        {"key": "pick", "type": "select", "label": "Pick"}, {"key": "when", "type": "date", "label": "When", "validation": {"max": "tomorrow"}},
        {"key": "kind_of", "type": "color", "label": "Colour"}, {"key": "content"}]}]}
    resp = root(world).post(f"{API}/admin/forms", json={"name": "Broken", "mode": "configurable", "definition": bad})

    assert resp.status_code == 422 and resp.json()["code"] == "INVALID_FORM"
    codes = {(e["field"].split(".")[-1], e["code"]) for e in resp.json()["errors"]}
    assert ("fields[0]", "ALWAYS_REQUIRED") in codes and ("key", "INVALID_KEY") in codes and ("options", "REQUIRED") in codes
    assert ("max", "INVALID_RULE") in codes and ("type", "INVALID_TYPE") in codes and ("key", "DUPLICATE") in codes
    assert any(e["code"] == "MISSING_CORE_FIELD" and "category" in e["message"] for e in resp.json()["errors"])


def test_a_form_name_is_unique_and_saving_over_someone_elses_save_is_refused(world):
    form = make_form(world)
    assert root(world).post(f"{API}/admin/forms", json={"name": "event POSTS", "mode": "standard"}).json()["code"] == "DUPLICATE_FORM_NAME"

    first = root(world).patch(f"{API}/admin/forms/{form['id']}", json={"description": "Mine", "base_revision": form["revision"]})
    assert first.status_code == 200 and first.json()["revision"] == form["revision"] + 1
    stale = root(world).patch(f"{API}/admin/forms/{form['id']}", json={"description": "Yours", "base_revision": form["revision"]})
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT" and stale.json()["current_version"] == form["revision"] + 1


def test_a_published_version_never_changes_and_the_draft_can_move_on(world):
    form = make_form(world)
    publish_form(world, form)
    edited = {"sections": [{"key": "a", "title": "A", "fields": [{"key": "title"}, {"key": "category"}, {"key": "content"}]}]}
    saved = root(world).patch(f"{API}/admin/forms/{form['id']}", json={"definition": edited}).json()
    assert saved["has_unpublished_changes"] is True and saved["current_version"] == 1
    assert len(my_form(world)["definition"]["sections"]) == 4                              # posts still get version 1

    publish_form(world, form, note="Simpler")
    versions = root(world).get(f"{API}/admin/forms/{form['id']}/versions").json()
    assert [v["version"] for v in versions["items"]] == [2, 1] and versions["items"][0]["note"] == "Simpler"
    v1 = root(world).get(f"{API}/admin/forms/{form['id']}/versions/1").json()
    assert v1["is_current"] is False and len(v1["definition"]["sections"]) == 4
    assert root(world).get(f"{API}/admin/forms/{form['id']}/versions/9").json()["code"] == "FORM_VERSION_NOT_FOUND"
    assert len(my_form(world)["definition"]["sections"]) == 1 and my_form(world)["version"] == 2


def test_the_form_preview_shows_the_fields_in_order_without_saving(world):
    unsaved = root(world).post(f"{API}/admin/forms/preview", json={"mode": "configurable", "definition": DEFINITION}).json()
    assert unsaved["source"] == "unsaved" and unsaved["field_count"] == 12 and "event_date" in unsaved["required_fields"]
    form = make_form(world)
    saved = root(world).get(f"{API}/admin/forms/{form['id']}/preview").json()
    assert saved["source"] == "draft" and saved["name"] == "Event posts"
    assert root(world).post(f"{API}/admin/forms/preview", json={"mode": "configurable", "definition": {"sections": []}}).status_code == 422


# --- forms: who gets which ----------------------------------------------------------------------------------------------------

def test_a_form_for_selected_businesses_beats_a_global_one_and_other_businesses_do_not_see_it(world):
    everyone = make_form(world, name="For everyone")
    publish_form(world, everyone)
    only_a = make_form(world, name="Only Calm Clinic", scope="selected", tenant_ids=[str(TENANT_A)])
    publish_form(world, only_a)

    assert my_form(world, world.people.asha)["form_id"] == only_a["id"]
    assert my_form(world, world.people.bela)["form_id"] == everyone["id"]
    assert my_form(world, world.people.asha, "news")["form_id"] == only_a["id"]
    assert root(world).get(f"{API}/admin/forms/{only_a['id']}").json()["assignments"] == [{"tenant_id": str(TENANT_A), "business_name": "Calm Clinic"}]

    seen = root(world).get(f"{API}/admin/forms/active", params={"tenant_id": str(TENANT_A)}).json()
    assert seen["business_name"] == "Calm Clinic" and seen["blog"]["form_id"] == only_a["id"] and seen["news"]["form_id"] == only_a["id"]
    assert root(world).get(f"{API}/admin/forms/active").json()["blog"]["form_id"] == everyone["id"]       # platform posts: global forms only

    assert root(world).put(f"{API}/admin/forms/{only_a['id']}/assignments", json={"scope": "selected", "tenant_ids": [str(TENANT_B)]}).status_code == 200
    assert my_form(world, world.people.asha)["form_id"] == everyone["id"] and my_form(world, world.people.bela)["form_id"] == only_a["id"]
    assert root(world).put(f"{API}/admin/forms/{only_a['id']}/assignments", json={"scope": "selected", "tenant_ids": []}).status_code == 422
    assert root(world).put(f"{API}/admin/forms/{only_a['id']}/assignments", json={"scope": "selected", "tenant_ids": [str(TENANT_A), "0b8f3a06-6f43-4b7b-9d4a-1c2a1e0b5a11"]}).status_code == 422


def test_a_form_for_one_kind_is_only_offered_for_that_kind_and_deactivating_it_falls_back(world):
    news_only = make_form(world, name="News only", form_type="news")
    publish_form(world, news_only)
    assert my_form(world, kind="news")["form_id"] == news_only["id"] and my_form(world, kind="blog")["form_id"] is None

    off = root(world).post(f"{API}/admin/forms/{news_only['id']}/deactivate").json()
    assert off["is_active"] is False and my_form(world, kind="news")["form_id"] is None
    assert root(world).post(f"{API}/admin/forms/{news_only['id']}/activate").json()["is_active"] is True
    never = make_form(world, name="Never published")
    assert root(world).post(f"{API}/admin/forms/{never['id']}/activate").json()["code"] == "FORM_NOT_PUBLISHED"


def test_the_newest_published_form_wins_between_equals(world):
    older = make_form(world, name="Older")
    publish_form(world, older)
    newer = make_form(world, name="Newer")
    publish_form(world, newer)
    assert my_form(world)["form_id"] == newer["id"]


def test_a_form_used_by_a_post_cannot_be_deleted(world):
    used, spare = make_form(world, name="Used"), make_form(world, name="Spare")
    publish_form(world, used)
    cat = make_category(world)
    draft(world, world.people.asha, cat, custom_fields={"event_date": "2026-12-01"})

    refused = root(world).delete(f"{API}/admin/forms/{used['id']}")
    assert refused.status_code == 409 and refused.json()["code"] == "FORM_IN_USE" and refused.json()["post_count"] == 1
    assert root(world).delete(f"{API}/admin/forms/{spare['id']}").status_code == 200
    assert root(world).get(f"{API}/admin/forms/{spare['id']}").json()["code"] == "FORM_NOT_FOUND"
    listed = root(world).get(f"{API}/admin/forms", params={"status": "published"}).json()
    assert [f["name"] for f in listed["items"]] == ["Used"] and listed["items"][0]["post_count"] == 1 and listed["pagination"]["total"] == 1


def test_only_the_super_admin_manages_forms(world):
    form = make_form(world)
    for user in (world.people.asha, world.people.priya, world.people.doc):
        assert world.as_user(user).get(f"{API}/admin/forms").status_code == 403
        assert world.as_user(user).post(f"{API}/admin/forms", json={"name": "X", "mode": "standard"}).status_code == 403
        assert world.as_user(user).post(f"{API}/admin/forms/{form['id']}/publish").status_code == 403
    assert world.as_user(None).get(f"{API}/admin/forms").status_code == 401
    assert world.as_user(world.people.root).get(f"{API}/my/form", params={"kind": "blog"}).status_code == 403     # Enterprise Admin only
    assert world.as_user(world.people.asha).get(f"{API}/my/form").status_code == 422                                 # kind is needed


# --- a post written against a form -------------------------------------------------------------------------------------------

def test_custom_values_are_saved_checked_and_required_before_the_post_is_sent(world):
    form = make_form(world)
    publish_form(world, form)
    cat = make_category(world)
    asha = world.people.asha

    post = draft(world, asha, cat, custom_fields={"audience": "kids", "budget": 20})
    assert post["form_id"] == form["id"] and post["form_version"] == 1 and post["custom_fields"] == {"audience": "kids", "budget": 20}
    assert post["form"]["name"] == "Event posts" and post["form"]["version"] == 1

    refused = world.as_user(asha).post(f"{API}/my/posts/{post['id']}/submit")
    assert refused.status_code == 422 and refused.json()["code"] == "VALIDATION_FAILED"
    assert refused.json()["detail"] == "Add the event date before you send this for approval"
    assert refused.json()["errors"] == [{"field": "custom_fields.event_date", "code": "REQUIRED", "message": "Event date is required"}]

    patched = world.as_user(asha).patch(f"{API}/my/posts/{post['id']}", json={"custom_fields": {"event_date": "2026-12-01", "budget": None}}).json()
    assert patched["custom_fields"] == {"audience": "kids", "event_date": "2026-12-01"}
    assert submit(world, asha, patched)["status"] == "pending_approval"


def test_bad_custom_values_are_refused_with_one_error_each(world):
    publish_form(world, make_form(world))
    cat = make_category(world)
    asha = world.people.asha
    post = draft(world, asha, cat)

    resp = world.as_user(asha).patch(f"{API}/my/posts/{post['id']}", json={"custom_fields": {
        "event_date": "next week", "audience": "pets", "budget": 5000, "nonsense": "x"}})
    assert resp.status_code == 422
    by_field = {e["field"]: e["code"] for e in resp.json()["errors"]}
    assert by_field == {"custom_fields.event_date": "INVALID_FORMAT", "custom_fields.audience": "INVALID_OPTION",
                        "custom_fields.budget": "TOO_LARGE", "custom_fields.nonsense": "UNKNOWN_FIELD"}
    assert world.as_user(asha).get(f"{API}/my/posts/{post['id']}").json()["custom_fields"] == {}          # nothing was saved
    long_title = world.as_user(asha).patch(f"{API}/my/posts/{post['id']}", json={"title": "x" * 100})
    assert long_title.status_code == 422 and long_title.json()["errors"][0] == {"field": "title", "code": "TOO_LONG", "message": "Title can have at most 80 characters"}


def test_enterprise_only_fields_are_not_for_platform_posts_and_internal_ones_stay_off_the_public_page(world):
    publish_form(world, make_form(world))
    cat = make_category(world)

    platform = root(world).post(f"{API}/admin/posts", json={"title": "Platform news", "kind": "news", "content": BODY, "category_id": cat["id"],
                                                             "excerpt": "x", "custom_fields": {"event_date": "2026-12-01", "partner": "Acme"}})
    assert platform.status_code == 422 and platform.json()["errors"][0]["field"] == "custom_fields.partner"

    live = live_merchant_post(world, cat, custom_fields={"event_date": "2026-12-01", "audience": "adults", "partner": "Acme", "editor_note": "Check the date"})
    public = world.as_user(None).get(f"{API}/{live['slug']}").json()
    assert public["custom_fields"] == [
        {"key": "event_date", "label": "Event date", "type": "date", "renderer": "date_picker", "value": "2026-12-01"},
        {"key": "audience", "label": "Audience", "type": "dropdown", "renderer": "select", "value": "adults"}]    # Partner and the editors' note are Enterprise Admin only
    assert "Check the date" not in str(public) and "Acme" not in str(public)
    for private in ("reviewer_note", "pending_revision", "form", "version", "form_id", "actions", "is_live"):
        assert private not in public
    assert public["supporting_documents"] == []                                                          # the form marks documents internal


def test_a_live_post_keeps_its_form_version_while_drafts_follow_the_active_one(world):
    form = make_form(world)
    publish_form(world, form)
    cat = make_category(world)
    asha = world.people.asha
    live = live_merchant_post(world, cat, custom_fields={"event_date": "2026-12-01"})
    waiting = draft(world, asha, cat, title="A later draft")
    assert live["form_version"] == 1 and waiting["form_version"] == 1

    simpler = {"sections": [{"key": "a", "title": "A", "fields": [{"key": "title"}, {"key": "category"}, {"key": "content"},
                                                                    {"key": "reading_time", "type": "number", "label": "Minutes", "required": True}]}]}
    root(world).patch(f"{API}/admin/forms/{form['id']}", json={"definition": simpler})
    publish_form(world, form)

    still = world.as_user(asha).get(f"{API}/my/posts/{live['id']}").json()
    assert still["form_version"] == 1 and still["form"]["version"] == 1 and "event_date" in str(still["form"]["definition"])
    edit = world.as_user(asha).patch(f"{API}/my/posts/{live['id']}", json={"title": "Edited live post"})
    assert edit.status_code == 200 and edit.json()["form_version"] == 1                                    # the edit is still version 1
    assert world.as_user(None).get(f"{API}/{live['slug']}").json()["custom_fields"][0]["label"] == "Event date"

    moved = world.as_user(asha).get(f"{API}/my/posts/{waiting['id']}").json()
    assert moved["form"]["version"] == 2 and moved["form_version"] == 1                                    # shown the active form; bound when saved
    saved = world.as_user(asha).patch(f"{API}/my/posts/{waiting['id']}", json={"custom_fields": {"reading_time": 5}})
    assert saved.status_code == 200 and saved.json()["form_version"] == 2 and saved.json()["custom_fields"]["reading_time"] == 5


def test_a_post_waiting_for_review_is_judged_by_the_form_it_was_sent_with(world):
    form = make_form(world)
    publish_form(world, form)
    cat = make_category(world)
    pending = draft(world, world.people.asha, cat, custom_fields={"event_date": "2026-12-01"})
    submit(world, world.people.asha, pending)

    stricter = {"sections": [{"key": "a", "title": "A", "fields": [{"key": "title"}, {"key": "category"}, {"key": "content"},
                                                                    {"key": "reading_time", "type": "number", "label": "Minutes", "required": True}]}]}
    root(world).patch(f"{API}/admin/forms/{form['id']}", json={"definition": stricter})
    publish_form(world, form)

    approved = review(world, pending, "approve")
    assert approved.status_code == 200 and approved.json()["form_version"] == 1


# --- the extra post fields ------------------------------------------------------------------------------------------------------

def test_images_links_and_documents_are_saved_and_the_public_sees_the_public_ones(world):
    cat = make_category(world)
    extras = {"additional_images": [{"url": "https://cdn.example.test/a.jpg", "alt": "A", "caption": "First"}],
              "reference_urls": [{"url": "https://example.org/source", "label": "Source"}],
              "supporting_documents": [{"url": "https://cdn.example.test/report.pdf", "name": "Report"}]}
    live = live_merchant_post(world, cat, **extras)
    assert live["additional_images"] == [{"url": "https://cdn.example.test/a.jpg", "alt": "A", "caption": "First"}]
    assert live["reference_urls"] == [{"url": "https://example.org/source", "label": "Source"}]

    public = world.as_user(None).get(f"{API}/{live['slug']}").json()
    assert public["additional_images"] == live["additional_images"] and public["reference_urls"] == live["reference_urls"]
    assert public["supporting_documents"] == [{"url": "https://cdn.example.test/report.pdf", "name": "Report", "size": None, "content_type": None}]   # Standard form: public

    bad = world.as_user(world.people.asha).post(f"{API}/my/posts", json={"title": "Bad", "reference_urls": [{"url": "/relative"}]})
    assert bad.status_code == 422
    too_many = world.as_user(world.people.asha).post(f"{API}/my/posts", json={
        "title": "Many", "additional_images": [{"url": f"https://cdn.example.test/{i}.jpg"} for i in range(settings.BLOG_MAX_ADDITIONAL_IMAGES + 1)]})
    assert too_many.status_code == 422


def test_a_form_can_switch_off_a_field_and_a_post_cannot_use_it(world):
    plain = {"sections": [{"key": "a", "title": "A", "fields": [{"key": "title"}, {"key": "category"}, {"key": "content"}, {"key": "cover_image", "visible": False}]}]}
    publish_form(world, make_form(world, definition=plain))
    cat = make_category(world)
    resp = world.as_user(world.people.asha).post(f"{API}/my/posts", json={"title": "With cover", "category_id": cat["id"], "cover_image_url": "https://cdn.example.test/c.jpg"})
    assert resp.status_code == 422 and resp.json()["errors"] == [{"field": "cover_image", "code": "NOT_IN_FORM", "message": "This form does not use cover image"}]
    assert world.as_user(world.people.asha).post(f"{API}/my/posts", json={"title": "Without", "category_id": cat["id"]}).status_code == 201


# --- versions, previews, revisions ----------------------------------------------------------------------------------------------

def test_every_change_raises_the_version_and_a_stale_copy_is_refused(world):
    cat = make_category(world)
    asha = world.people.asha
    post = draft(world, asha, cat)
    shown = world.as_user(asha).get(f"{API}/my/posts/{post['id']}")
    assert post["version"] == 1 and shown.headers["ETag"] == '"1"'

    saved = world.as_user(asha).patch(f"{API}/my/posts/{post['id']}", json={"excerpt": "Changed", "base_version": 1})
    assert saved.status_code == 200 and saved.json()["version"] == 2 and saved.headers["ETag"] == '"2"'

    stale = world.as_user(asha).patch(f"{API}/my/posts/{post['id']}", json={"excerpt": "Mine"})                    # no version sent: allowed
    assert stale.status_code == 200 and stale.json()["version"] == 3
    refused = world.as_user(asha).patch(f"{API}/my/posts/{post['id']}", json={"excerpt": "Old copy", "base_version": 1})
    assert refused.status_code == 409 and refused.json()["code"] == "VERSION_CONFLICT" and refused.json()["current_version"] == 3
    header = world.as_user(asha).post(f"{API}/my/posts/{post['id']}/submit", headers={"If-Match": '"2"'})
    assert header.status_code == 409 and header.json()["current_version"] == 3
    assert world.as_user(asha).post(f"{API}/my/posts/{post['id']}/submit", headers={"If-Match": "soon"}).json()["code"] == "INVALID_IF_MATCH"
    assert world.as_user(asha).get(f"{API}/my/posts/{post['id']}").json()["excerpt"] == "Mine"                    # the refused change did nothing
    ok = world.as_user(asha).post(f"{API}/my/posts/{post['id']}/submit", headers={"If-Match": '"3"'})
    assert ok.status_code == 200 and ok.json()["version"] == 4


def test_a_decision_made_on_an_old_copy_is_refused(world):
    cat = make_category(world)
    post = draft(world, world.people.asha, cat)
    sent = submit(world, world.people.asha, post)

    stale = world.as_user(world.people.root).post(f"{API}/admin/posts/{post['id']}/review", json={"action": "approve", "base_version": sent["version"] - 1})
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT"
    fresh = world.as_user(world.people.root).post(f"{API}/admin/posts/{post['id']}/review", json={"action": "approve"}, headers={"If-Match": f'"{sent["version"]}"'})
    assert fresh.status_code == 200 and fresh.json()["status"] == "published"


def test_titles_and_slugs_cannot_repeat(world):
    cat = make_category(world)
    asha, bela = world.people.asha, world.people.bela
    first = world.as_user(asha).post(f"{API}/my/posts", json={"title": "Same Title", "category_id": cat["id"]})
    assert first.status_code == 201

    clash = world.as_user(asha).post(f"{API}/my/posts", json={"title": "same title", "category_id": cat["id"]})
    assert clash.status_code == 409 and clash.json()["code"] == "DUPLICATE_TITLE" and clash.json()["field"] == "title"
    assert world.as_user(asha).post(f"{API}/my/posts", json={"title": "Same Title", "kind": "news"}).status_code == 201     # another kind
    assert world.as_user(bela).post(f"{API}/my/posts", json={"title": "Same Title"}).status_code == 201                       # another business
    other = world.as_user(asha).post(f"{API}/my/posts", json={"title": "Other"}).json()
    rename = world.as_user(asha).patch(f"{API}/my/posts/{other['id']}", json={"title": "SAME TITLE"})
    assert rename.status_code == 409 and rename.json()["code"] == "DUPLICATE_TITLE"
    assert world.as_user(asha).patch(f"{API}/my/posts/{other['id']}", json={"title": "Other"}).status_code == 200               # its own title

    mine = world.as_user(bela).get(f"{API}/my/posts").json()["items"][0]
    assert mine["slug"].startswith("same-title")                                                                               # slugs are made unique

    made = root(world).post(f"{API}/admin/posts", json={"title": "Platform", "slug": "my-page"})
    assert made.status_code == 201 and made.json()["slug"] == "my-page"
    again = root(world).post(f"{API}/admin/posts", json={"title": "Platform two", "slug": "my-page"})
    assert again.status_code == 409 and again.json()["code"] == "DUPLICATE_SLUG" and again.json()["field"] == "slug"
    assert root(world).post(f"{API}/admin/posts", json={"title": "Platform three", "slug": "categories"}).json()["code"] == "RESERVED_SLUG"
    assert root(world).post(f"{API}/admin/posts", json={"title": "Platform four", "slug": "!!!"}).json()["code"] == "INVALID_SLUG"
    settings.BLOG_REQUIRE_UNIQUE_TITLES = False
    try:
        assert world.as_user(asha).post(f"{API}/my/posts", json={"title": "Same Title", "category_id": cat["id"]}).status_code == 201
    finally:
        settings.BLOG_REQUIRE_UNIQUE_TITLES = True


def test_a_preview_shows_what_a_reader_would_see_and_what_is_still_missing(world):
    publish_form(world, make_form(world))
    cat = make_category(world)
    asha = world.people.asha

    unsaved = world.as_user(asha).post(f"{API}/my/posts/preview", json={
        "title": "Unsaved", "category_id": cat["id"], "content": "<p>Hello</p><script>x()</script>", "excerpt": "e",
        "custom_fields": {"audience": "kids", "editor_note": "internal"}}).json()
    assert unsaved["preview"] is True and unsaved["id"] is None and unsaved["content"] == "<p>Hello</p>" and unsaved["business_name"] == "Calm Clinic"
    assert unsaved["custom_fields"] == [{"key": "audience", "label": "Audience", "type": "dropdown", "renderer": "select", "value": "kids"}]
    assert unsaved["ready_to_submit"] is False and unsaved["problems"] == [{"field": "custom_fields.event_date", "code": "REQUIRED", "message": "Event date is required"}]
    assert world.as_user(asha).get(f"{API}/my/posts").json()["pagination"]["total"] == 0                       # nothing was stored
    assert world.as_user(asha).post(f"{API}/my/posts/preview", json={"custom_fields": {"nope": 1}}).status_code == 422

    saved = draft(world, asha, cat, excerpt="e", custom_fields={"event_date": "2026-12-01"})
    shown = world.as_user(asha).get(f"{API}/my/posts/{saved['id']}/preview").json()
    assert shown["id"] == saved["id"] and shown["ready_to_submit"] is True and shown["status"] == "draft" and shown["category"] == "Health"
    assert world.as_user(world.people.bela).get(f"{API}/my/posts/{saved['id']}/preview").status_code == 404
    assert world.as_user(root_user(world)).get(f"{API}/admin/posts/{saved['id']}/preview").json()["title"] == saved["title"]
    platform = root(world).post(f"{API}/admin/posts/preview", json={"title": "Platform preview"}).json()
    assert platform["author_type"] == "platform" and platform["author_name"] == "Platform Root" and platform["business_name"] is None


def root_user(w):
    return w.people.root


def test_revisions_keep_the_content_after_each_change_and_the_history_has_versions(world):
    cat = make_category(world)
    asha = world.people.asha
    post = draft(world, asha, cat, title="Version one")
    world.as_user(asha).patch(f"{API}/my/posts/{post['id']}", json={"title": "Version two"})
    sent = submit(world, asha, {"id": post["id"]})
    review(world, post, "approve")
    edit = world.as_user(asha).patch(f"{API}/my/posts/{post['id']}", json={"title": "Version three"}).json()
    assert edit["pending_revision"]["changed_fields"] == ["title"] and edit["live_version"]["title"] == "Version two"

    revs = root(world).get(f"{API}/admin/posts/{post['id']}/revisions").json()
    assert [(r["action"], r["title"]) for r in revs["items"]] == [
        ("submitted", "Version three"), ("edited", "Version three"), ("approved", "Version two"), ("submitted", "Version two"),
        ("edited", "Version two"), ("created", "Version one")]
    versions = [r["post_version"] for r in revs["items"]]
    assert versions[0] == versions[1]                                   # an edit of a live post and its submission are one change
    assert versions[1:] == sorted(versions[1:], reverse=True) and len(set(versions)) == len(versions) - 1
    detail = root(world).get(f"{API}/admin/posts/{post['id']}/revisions/{revs['items'][-1]['id']}").json()
    assert detail["snapshot"]["title"] == "Version one" and detail["snapshot"]["form"] == {"form_id": None, "version": None}
    assert world.as_user(asha).get(f"{API}/my/posts/{post['id']}/revisions").json()["total"] == revs["total"]
    assert world.as_user(world.people.bela).get(f"{API}/my/posts/{post['id']}/revisions").status_code == 404
    assert root(world).get(f"{API}/admin/posts/{post['id']}/revisions/{post['id']}").json()["code"] == "REVISION_NOT_FOUND"

    history = root(world).get(f"{API}/admin/posts/{post['id']}/history").json()["items"]
    assert all(h["post_version"] for h in history) and history[0]["post_version"] == edit["version"]
    assert {"action", "from_status", "to_status", "actor_id", "actor_name", "actor_role", "reason", "created_at"} <= set(history[0])
    assert sent["version"] >= 3


def test_a_super_admin_always_gives_a_reason_to_delete_and_it_is_kept(world):
    cat = make_category(world)
    platform = root(world).post(f"{API}/admin/posts", json={"title": "Old news", "status": "draft"}).json()

    assert root(world).delete(f"{API}/admin/posts/{platform['id']}").json()["code"] == "REASON_REQUIRED"
    assert root(world).delete(f"{API}/admin/posts/{platform['id']}", params={"reason": "Out of date"}).status_code == 200
    assert root(world).get(f"{API}/admin/posts/{platform['id']}").json()["code"] == "POST_NOT_FOUND"
    deleted = root(world).get(f"{API}/admin/posts", params={"status": "deleted"}).json()["items"]
    assert [p["title"] for p in deleted] == ["Old news"] and deleted[0]["reviewer_note"] == "Out of date"


# --- notifications --------------------------------------------------------------------------------------------------------------

def test_notifications_carry_the_details_and_a_link_for_the_reader(world):
    cat = make_category(world)
    asha = world.people.asha
    post = draft(world, asha, cat, title="Notify me")
    submit(world, asha, post)

    [sent] = notices_of(world, "blog_post_submitted")
    meta = sent["metadata"]
    assert meta["post_id"] == post["id"] and meta["post_type"] == "blog" and meta["title"] == "Notify me" and meta["business_name"] == "Calm Clinic"
    assert meta["enterprise_id"] == post["enterprise_id"] and meta["author_name"] == "Asha Merchant" and meta["author_id"] == post["author_id"]
    assert meta["occurred_at"].endswith("Z") and meta["deep_link"] == settings.BLOG_DEEP_LINK_SUPER_ADMIN.replace("{post_id}", post["id"])

    review(world, post, "request_changes", "Add a source")
    [asked] = notices_of(world, "blog_post_changes_requested")
    assert asked["metadata"]["reason"] == "Add a source" and asked["metadata"]["deep_link"] == settings.BLOG_DEEP_LINK_ENTERPRISE.replace("{post_id}", post["id"])
    assert asked["user_ids"] == [post["author_id"]] or [str(u) for u in asked["user_ids"]] == [post["author_id"]]

    submit(world, asha, post)
    review(world, post, "reject", "Not suitable")
    assert notices_of(world, "blog_post_rejected")[0]["metadata"]["reason"] == "Not suitable"
    submit(world, asha, post)
    review(world, post, "approve")
    assert len(notices_of(world, "blog_post_approved")) == 1
    root(world).post(f"{API}/admin/posts/{post['id']}/unpublish", json={"reason": "Outdated"})
    assert notices_of(world, "blog_post_unpublished")[0]["metadata"]["reason"] == "Outdated"
    root(world).post(f"{API}/admin/posts/{post['id']}/publish")
    assert len(notices_of(world, "blog_post_published")) == 1
    root(world).delete(f"{API}/admin/posts/{post['id']}", params={"reason": "Policy"})
    assert notices_of(world, "blog_post_deleted")[0]["metadata"]["reason"] == "Policy"


# --- uploads ---------------------------------------------------------------------------------------------------------------------------

def png(size=(20, 20)):
    buffer = io.BytesIO()
    Image.new("RGB", size, "blue").save(buffer, "PNG")
    return buffer.getvalue()


def upload(w, user, name, data, content_type, purpose):
    return w.as_user(user).post(f"{API}/uploads", data={"purpose": purpose}, files={"file": (name, data, content_type)})


@pytest.fixture
def uploads_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    return tmp_path


def test_an_image_upload_is_checked_stored_and_described(world, uploads_dir):
    resp = upload(world, world.people.asha, "cover.png", png((30, 20)), "image/png", "cover_image")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["content_type"] == "image/png" and body["width"] == 30 and body["height"] == 20 and body["purpose"] == "cover_image" and body["name"] == "cover.png"
    assert body["url"].endswith(f"/api/v1/cms/blogs/files/{body['file_id']}") and body["size"] > 0 and body["scan_status"]
    assert (uploads_dir / "blogs").is_dir() and len(list((uploads_dir / "blogs").iterdir())) == 1

    limits = world.as_user(world.people.asha).get(f"{API}/uploads/limits").json()
    assert limits["image_max_mb"] == settings.BLOG_MAX_IMAGE_MB and "pdf" in limits["document_extensions"] and "image/webp" in limits["image_types"]
    assert world.as_user(world.people.priya).get(f"{API}/uploads/limits").status_code == 403
    assert world.as_user(None).get(f"{API}/uploads/limits").status_code == 401


def test_upload_errors_say_what_is_wrong(world, uploads_dir, monkeypatch):
    asha = world.people.asha
    not_image = upload(world, asha, "cover.png", b"this is text", "image/png", "cover_image")
    assert not_image.status_code == 415 and not_image.json()["code"] == "UNSUPPORTED_TYPE"
    assert upload(world, asha, "cover.png", b"", "image/png", "image").json()["code"] == "EMPTY_FILE"
    assert upload(world, asha, "cover.png", png(), "image/png", "banner").status_code == 422
    assert upload(world, asha, "x.svg", b"<svg/>", "image/svg+xml", "image").json()["code"] == "UNSUPPORTED_TYPE"
    assert upload(world, asha, "x.exe", b"MZ" + b"0" * 50, "application/octet-stream", "document").json()["code"] == "UNSUPPORTED_TYPE"
    fake_pdf = upload(world, asha, "report.pdf", b"not a pdf", "application/pdf", "document")
    assert fake_pdf.status_code == 415 and "pdf" in fake_pdf.json()["detail"]
    wrong_ext = upload(world, asha, "report.docx", b"%PDF-1.4 hello", "application/pdf", "document")
    assert wrong_ext.status_code == 415
    assert world.as_user(None).post(f"{API}/uploads", data={"purpose": "image"}, files={"file": ("a.png", png(), "image/png")}).status_code == 401
    assert world.as_user(world.people.priya).post(f"{API}/uploads", data={"purpose": "image"}, files={"file": ("a.png", png(), "image/png")}).status_code == 403

    monkeypatch.setattr(settings, "BLOG_MAX_IMAGE_MB", 0)
    big = upload(world, asha, "cover.png", png(), "image/png", "image")
    assert big.status_code == 413 and big.json()["code"] == "FILE_TOO_LARGE" and big.json()["max_mb"] == 0
    monkeypatch.setattr(settings, "BLOG_MAX_DOCUMENT_MB", 0)
    assert upload(world, asha, "r.pdf", b"%PDF-1.4 hello", "application/pdf", "document").status_code == 413


def test_documents_are_checked_by_their_content(world, uploads_dir):
    asha = world.people.asha
    pdf = upload(world, asha, "Annual report.pdf", b"%PDF-1.4\n1 0 obj\n", "application/pdf", "document")
    assert pdf.status_code == 201 and pdf.json()["content_type"] == "application/pdf" and pdf.json()["purpose"] == "document"
    assert upload(world, asha, "notes.txt", "plain notes".encode(), "text/plain", "document").status_code == 201
    assert upload(world, asha, "data.csv", b"a,b\n1,2\n", "text/csv", "document").status_code == 201
    assert upload(world, asha, "old.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 20, "application/msword", "document").status_code == 201
    zipped = io.BytesIO()
    import zipfile
    with zipfile.ZipFile(zipped, "w") as z:
        z.writestr("[Content_Types].xml", "<x/>")
        z.writestr("word/document.xml", "<x/>")
    assert upload(world, asha, "new.docx", zipped.getvalue(), "application/octet-stream", "document").status_code == 201
    assert upload(world, asha, "new.xlsx", zipped.getvalue(), "application/octet-stream", "document").status_code == 415      # a Word file named .xlsx


def test_an_uploaded_file_is_private_until_a_live_post_uses_it(world, uploads_dir):
    cat = make_category(world)
    asha, bela = world.people.asha, world.people.bela
    cover = upload(world, asha, "cover.png", png(), "image/png", "cover_image").json()
    path = f"{API}/files/{cover['file_id']}"

    assert world.as_user(None).get(path).status_code == 404
    assert world.as_user(bela).get(path).json()["code"] == "FILE_NOT_FOUND"
    mine = world.as_user(asha).get(path)
    assert mine.status_code == 200 and mine.headers["content-type"] == "image/png" and mine.headers["x-content-type-options"] == "nosniff"
    assert mine.headers["content-disposition"].startswith("inline")
    assert world.as_user(world.people.root).get(path).status_code == 200

    stolen = world.as_user(bela).post(f"{API}/my/posts", json={"title": "Taking it", "cover_image_url": cover["url"]})
    assert stolen.status_code == 422 and stolen.json()["errors"][0]["code"] == "FILE_NOT_FOUND"

    post = draft(world, asha, cat, cover_image_url=cover["url"])
    assert post["cover_image_url"] == cover["url"]
    assert world.as_user(None).get(path).status_code == 404                                          # a draft does not make it public
    submit(world, asha, post)
    review(world, post, "approve")
    assert world.as_user(None).get(path).status_code == 200                                          # live: anyone
    root(world).post(f"{API}/admin/posts/{post['id']}/unpublish", json={"reason": "Pulled"})
    assert world.as_user(None).get(path).status_code == 404


def test_a_document_is_attached_with_its_name_and_downloaded(world, uploads_dir):
    cat = make_category(world)
    asha = world.people.asha
    doc = upload(world, asha, "Annual report.pdf", b"%PDF-1.4\nbody", "application/pdf", "document").json()
    image = upload(world, asha, "pic.png", png(), "image/png", "image").json()

    wrong = world.as_user(asha).post(f"{API}/my/posts", json={"title": "Wrong slot", "supporting_documents": [{"url": image["url"]}]})
    assert wrong.status_code == 422 and wrong.json()["errors"][0]["code"] == "WRONG_FILE_KIND"
    post = draft(world, asha, cat, supporting_documents=[{"url": doc["url"]}], additional_images=[{"url": image["url"], "alt": "Pic"}])
    assert post["supporting_documents"] == [{"url": doc["url"], "name": "Annual report.pdf", "size": doc["size"], "content_type": "application/pdf"}]

    submit(world, asha, post)
    review(world, post, "approve")
    public = world.as_user(None).get(f"{API}/{post['slug']}").json()
    assert public["supporting_documents"][0]["name"] == "Annual report.pdf" and public["additional_images"][0]["alt"] == "Pic"
    download = world.as_user(None).get(f"{API}/files/{doc['file_id']}")
    assert download.status_code == 200 and download.headers["content-disposition"].startswith("attachment") and download.content == b"%PDF-1.4\nbody"


# --- media custom fields (image, media_upload) -----------------------------------------------------------------------------------------

MEDIA_DEFINITION = {"sections": [
    {"key": "basics", "title": "Basics", "fields": [{"key": "title"}, {"key": "category"}, {"key": "excerpt"}, {"key": "content"}]},
    {"key": "media", "title": "Images", "fields": [
        {"key": "field_media_upload_2779", "type": "media_upload", "label": "Additional Images", "validation": {"max_items": 2}},
        {"key": "hero", "type": "image", "label": "Hero picture"}]},
]}


def media_form(world):
    publish_form(world, make_form(world, definition=MEDIA_DEFINITION))


def urls_of(value):
    return [item["url"] for item in value]


def test_media_fields_keep_their_type_in_the_form_and_in_the_public_post(world, uploads_dir):
    media_form(world)
    cat = make_category(world)
    asha = world.people.asha
    first = upload(world, asha, "a.png", png(), "image/png", "image").json()
    second = upload(world, asha, "b.png", png((30, 30)), "image/png", "image").json()

    types = {f["key"]: f["type"] for s in my_form(world)["definition"]["sections"] for f in s["fields"]}
    assert types["field_media_upload_2779"] == "additional_images" and types["hero"] == "additional_images"       # the older image / media_upload names became the explicit type

    post = draft(world, asha, cat, custom_fields={"field_media_upload_2779": [first["url"], second["url"]], "hero": first["url"]})
    assert urls_of(post["custom_fields"]["field_media_upload_2779"]) == [first["url"], second["url"]] and urls_of(post["custom_fields"]["hero"]) == [first["url"]]

    submit(world, asha, post)
    live = review(world, post, "approve").json()
    public = world.as_user(None).get(f"{API}/{live['slug']}").json()
    by_key = {f["key"]: f for f in public["custom_fields"]}
    assert by_key["hero"]["type"] == "additional_images" and by_key["hero"]["renderer"] == "image_uploader"
    assert urls_of(by_key["field_media_upload_2779"]["value"]) == [first["url"], second["url"]]
    assert "file_id" not in str(public["custom_fields"])
    assert world.as_user(None).get(f"{API}/files/{second['file_id']}").status_code == 200           # a live post makes its custom pictures public


def test_a_media_field_takes_one_url_or_a_list_up_to_its_limit(world, uploads_dir):
    media_form(world)
    cat = make_category(world)
    asha = world.people.asha
    mine = upload(world, asha, "a.png", png(), "image/png", "image").json()
    post = draft(world, asha, cat)

    def patch(values):
        return world.as_user(asha).patch(f"{API}/my/posts/{post['id']}", json={"custom_fields": values})

    assert urls_of(patch({"field_media_upload_2779": mine["url"]}).json()["custom_fields"]["field_media_upload_2779"]) == [mine["url"]]
    dup = patch({"field_media_upload_2779": [mine["url"], mine["url"]]})
    assert dup.status_code == 422 and dup.json()["errors"][0]["code"] == "DUPLICATE_ITEM"
    too_many = patch({"field_media_upload_2779": ["https://x.test/1.png", "https://x.test/2.png", "https://x.test/3.png"]})
    assert too_many.status_code == 422 and too_many.json()["errors"][0]["code"] == "TOO_MANY"
    assert patch({"hero": ["https://x.test/1.png", "https://x.test/2.png"]}).json()["errors"][0]["code"] == "TOO_MANY"          # the old `image` takes one
    assert patch({"hero": "not a url"}).json()["errors"][0]["code"] == "INVALID" and patch({"hero": "javascript:alert(1)"}).json()["errors"][0]["code"] == "UNSAFE_SCHEME"
    assert patch({"field_media_upload_2779": [mine["url"], 5]}).json()["errors"][0]["code"] == "INVALID_FORMAT"
    assert patch({"hero": None}).status_code == 200


def test_pictures_in_a_media_field_must_be_the_businesss_own_and_an_image(world, uploads_dir):
    media_form(world)
    cat = make_category(world)
    asha, bela = world.people.asha, world.people.bela
    cover = upload(world, asha, "a.png", png(), "image/png", "image").json()
    pdf = upload(world, asha, "r.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf", "document")
    post = draft(world, bela, cat)

    stolen = world.as_user(bela).patch(f"{API}/my/posts/{post['id']}", json={"custom_fields": {"hero": cover["url"]}})
    assert stolen.status_code == 422 and stolen.json()["errors"][0]["code"] == "FILE_NOT_FOUND"
    assert pdf.status_code == 201, pdf.text
    wrong = world.as_user(asha).patch(f"{API}/my/posts/{draft(world, asha, cat)['id']}", json={"custom_fields": {"hero": pdf.json()["url"]}})
    assert wrong.json()["errors"][0]["code"] == "WRONG_FILE_KIND"


def test_the_form_builder_accepts_the_media_types_and_their_rules(world):
    bad = {"sections": [{"key": "s", "title": "S", "fields": [
        {"key": "title"}, {"key": "category"}, {"key": "content"},
        {"key": "pics", "type": "additional_images", "label": "Pictures", "validation": {"max_items": 99}},
        {"key": "docs", "type": "supporting_document", "label": "Documents", "validation": {"allowed_types": ["exe"]}}]}]}

    resp = root(world).post(f"{API}/admin/forms", json={"name": "Media", "mode": "configurable", "definition": bad})

    assert resp.status_code == 422
    assert {e["field"] for e in resp.json()["errors"]} >= {"definition.sections[0].fields[3].validation.max_items", "definition.sections[0].fields[4].validation.allowed_types"}
