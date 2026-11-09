"""Building Event and Training forms in the common contract: field and section operations on the draft, protected and built-in fields, the new field types and their
values, enterprise-only values kept out of the public column, review and submit, and publishing."""
import copy
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.form_builder import legacy_sections
from app.models.event_form_config_model import EventFormAssignment, EventFormAudit, EventFormConfiguration, EventFormConfigurationVersion
from app.models.training_form_config_model import TrainingFormConfiguration, TrainingFormConfigurationVersion
from app.services import event_form_config_service as event_svc
from app.services import form_common_write as write
from app.services.event_form_registry import DOMAIN_REQUIRED_CORE_KEYS as EVENT_REQUIRED, REGISTRY_BY_KEY as EVENT_REGISTRY
from event_sql_support import client_for, make_enterprise, make_event, make_registration, make_session, reset_overrides, super_admin_user
from tests.test_form_common_view import SECTIONS, core, custom

API = "/api/v1/events"
BASE = f"{API}/form-configuration/admin"


@pytest.fixture
def env(monkeypatch):
    for target in ("app.services.super_admin_identity.fetch_internal_user_by_id", "app.services.super_admin_identity.fetch_auth_me_profile",
                   "app.services.invigorate_auth_client.fetch_tenant_me_profile", "app.services.invigorate_auth_client.fetch_auth_me_profile"):
        monkeypatch.setattr(target, lambda *a, **k: None)
    db = make_session()
    EventFormAudit.__table__.create(bind=db.get_bind(), checkfirst=True)
    EventFormAssignment.__table__.create(bind=db.get_bind(), checkfirst=True)
    sections = copy.deepcopy(SECTIONS[:2])
    config = EventFormConfiguration(name="Summit form", scope="selective", status="draft", is_active=False, current_version=1)
    db.add(config)
    db.flush()
    draft = EventFormConfigurationVersion(configuration_id=config.id, version=1, status="draft", sections=sections)
    db.add(draft)
    db.commit()
    root = super_admin_user()
    yield SimpleNamespace(db=db, config=config, draft=draft, root=root, client=client_for(db, root), monkeypatch=monkeypatch)
    reset_overrides()
    db.close()


def common(env, path="", **kw):
    return env.client.get(f"{BASE}/{env.config.id}/common{path}", **kw)


def fields_of(body):
    return {f["key"]: f for s in body["definition"]["sections"] for f in s["fields"]}


def section_id(body, key):
    return next(s["id"] for s in body["definition"]["sections"] if s["key"] == key)


def stored(env):
    env.db.refresh(env.draft)
    return {f.get("stable_key") or f.get("core_key"): f for s in env.draft.sections for f in s["fields"]}


# --- fields and sections -----------------------------------------------------------------------------------------------------

def test_new_fields_of_every_kind_are_added_with_explicit_types_and_stored_with_their_type(env):
    view = env.client.get(f"{BASE}/{env.config.id}/common").json() if False else env.client.get(f"{BASE}/{env.config.id}/common")
    assert view.status_code == 200
    body = view.json()
    extra = section_id(body, "extra")
    added = {}
    for key, ftype, over in (("bio", "rich_text", {}), ("kickoff", "datetime", {"validation": {"not_in_past": True}}), ("catering", "toggle", {}), ("flavour", "radio", {"options": [{"value": "a"}, {"value": "b"}]}),
                             ("doors", "time", {"validation": {"min": "07:00"}}), ("intro", "video", {"validation": {"max_items": 1}, "visibility": "published"}),
                             ("slides", "supporting_document", {}), ("photos", "additional_images", {"validation": {"max_items": 3}}), ("hashtags", "tags", {})):
        resp = env.client.post(f"{BASE}/{env.config.id}/common/fields", json={"section_id": extra, "field": {"key": key, "label": key.title(), "type": ftype, **over}})
        assert resp.status_code == 201, (key, resp.text)
        added[key] = resp.json()
    last = added["hashtags"]
    got = fields_of(last)
    assert {k: (got[k]["type"], got[k]["renderer"]) for k in added} == {
        "bio": ("rich_text", "rich_text_editor"), "kickoff": ("datetime", "datetime_picker"), "catering": ("toggle", "switch"), "flavour": ("radio", "radio_group"),
        "doors": ("time", "time_picker"), "intro": ("video", "video_input"), "slides": ("supporting_document", "document_uploader"), "photos": ("additional_images", "image_uploader"),
        "hashtags": ("tags", "tag_input")}
    assert last["change"]["action"] == "field_added" and last["change"]["key"] == "hashtags" and last["change"]["field_id"] == got["hashtags"]["id"]
    assert got["kickoff"]["module_type"] is True and "module_type" not in got["bio"] and got["kickoff"]["kind"] == "custom"

    rows = stored(env)
    assert rows["kickoff"]["field_type"] == "datetime" and rows["kickoff"]["renderer"] == "datetime" and rows["kickoff"]["value_type"] == "string"
    assert rows["bio"]["renderer"] == "rich_text_editor" and rows["bio"]["source"] == "custom" and rows["bio"]["stable_key"] == "bio" and rows["catering"]["value_type"] == "boolean"
    assert rows["speaker"] == next(f for s in SECTIONS[:2] for f in s["fields"] if f.get("stable_key") == "speaker") | {"position": 1}      # a field nobody touched is stored as it was
    assert env.client.get(f"{BASE}/{env.config.id}/common/field-types").status_code == 200
    kinds = {t["type"] for t in env.client.get(f"{BASE}/{env.config.id}/common/field-types").json()}
    assert {"datetime", "toggle", "video", "tags"} <= kinds


def test_editing_moving_duplicating_reordering_and_deleting(env):
    body = common(env).json()
    basics, extra = section_id(body, "basics"), section_id(body, "extra")

    edited = env.client.patch(f"{BASE}/{env.config.id}/common/fields/speaker", json={"patch": {"label": "Main speaker", "help_text": "Full name", "validation": {"max_length": 80}, "visibility": "enterprise_admin_only"}})
    assert edited.status_code == 200, edited.text
    speaker = fields_of(edited.json())["speaker"]
    assert speaker["label"] == "Main speaker" and speaker["validation"] == {"max_length": 80} and speaker["visibility"] == "enterprise_admin_only" and speaker["required"] is True
    assert stored(env)["speaker"]["visibility"] == "enterprise_admin_only" and stored(env)["speaker"]["renderer"] == "text"                   # the older renderer name is kept
    assert env.client.patch(f"{BASE}/{env.config.id}/common/fields/speaker", json={"patch": {"type": "number"}}).json()["errors"][0]["code"] == "TYPE_IMMUTABLE"
    assert env.client.patch(f"{BASE}/{env.config.id}/common/fields/speaker", json={"patch": {"key": "renamed"}}).json()["errors"][0]["code"] == "KEY_IMMUTABLE"

    copy_ = env.client.post(f"{BASE}/{env.config.id}/common/fields/audience/duplicate")
    assert copy_.status_code == 201 and "audience_copy" in fields_of(copy_.json()) and fields_of(copy_.json())["audience_copy"]["label"] == "Audience (copy)"
    assert stored(env)["audience_copy"]["options"] == [{"value": "kids", "label": "Kids"}, {"value": "adults", "label": "Adults"}]

    moved = env.client.post(f"{BASE}/{env.config.id}/common/fields/audience_copy/move", json={"section_id": basics, "position": 1})
    assert moved.status_code == 200 and [f["key"] for f in moved.json()["definition"]["sections"][0]["fields"]][0] == "audience_copy"
    ids = [f["id"] for f in moved.json()["definition"]["sections"][0]["fields"]]
    ordered = env.client.put(f"{BASE}/{env.config.id}/common/sections/{basics}/fields/order", json={"ids": list(reversed(ids))})
    assert [f["id"] for f in ordered.json()["definition"]["sections"][0]["fields"]] == list(reversed(ids))
    assert env.client.put(f"{BASE}/{env.config.id}/common/sections/{basics}/fields/order", json={"ids": ids[:1]}).json()["code"] == "INVALID_ORDER"

    gone = env.client.delete(f"{BASE}/{env.config.id}/common/fields/audience_copy")
    assert gone.status_code == 200 and "audience_copy" not in fields_of(gone.json()) and "audience_copy" not in stored(env)

    sec = env.client.post(f"{BASE}/{env.config.id}/common/sections", json={"title": "Logistics", "position": 1})
    assert sec.status_code == 201 and [s["title"] for s in sec.json()["definition"]["sections"]] == ["Logistics", "Basics", "Extra"]
    sid = section_id(sec.json(), "logistics")
    assert env.client.patch(f"{BASE}/{env.config.id}/common/sections/{sid}", json={"title": "Travel"}).json()["definition"]["sections"][0]["title"] == "Travel"
    swapped = env.client.put(f"{BASE}/{env.config.id}/common/sections/order", json={"ids": [s["id"] for s in reversed(sec.json()["definition"]["sections"])]})
    assert [s["key"] for s in swapped.json()["definition"]["sections"]][0] == "extra"
    assert env.client.delete(f"{BASE}/{env.config.id}/common/sections/{sid}").status_code == 200                                         # empty: deleted
    assert env.client.delete(f"{BASE}/{env.config.id}/common/sections/{extra}").json()["code"] == "SECTION_NOT_EMPTY"
    assert env.client.delete(f"{BASE}/{env.config.id}/common/sections/{basics}").json()["code"] == "SECTION_HAS_PROTECTED_FIELDS"          # it holds protected fields
    moved_away = env.client.delete(f"{BASE}/{env.config.id}/common/sections/{basics}", params={"move_to": extra})                        # ... which may move, never be deleted
    assert moved_away.status_code == 200 and "title" in fields_of(moved_away.json())


def test_built_in_and_protected_fields_are_guarded(env):
    body = common(env).json()
    title = fields_of(body)["title"]
    assert title["system"] is True and fields_of(body)["tags"]["system"] is False

    for call, code in ((lambda: env.client.delete(f"{BASE}/{env.config.id}/common/fields/title"), "PROTECTED_FIELD"),
                       (lambda: env.client.delete(f"{BASE}/{env.config.id}/common/fields/tags"), "CORE_FIELD"),
                       (lambda: env.client.post(f"{BASE}/{env.config.id}/common/fields/tags/duplicate"), "CORE_FIELD"),
                       (lambda: env.client.patch(f"{BASE}/{env.config.id}/common/fields/title", json={"patch": {"enabled": False}}), "PROTECTED_FIELD"),
                       (lambda: env.client.patch(f"{BASE}/{env.config.id}/common/fields/start_date", json={"patch": {"required": False}}), "PROTECTED_FIELD"),
                       (lambda: env.client.patch(f"{BASE}/{env.config.id}/common/fields/tags", json={"patch": {"type": "short_text"}}), "CORE_FIELD"),
                       (lambda: env.client.patch(f"{BASE}/{env.config.id}/common/fields/tags", json={"patch": {"validation": {"max_items": 2}}}), "CORE_FIELD"),
                       (lambda: env.client.post(f"{BASE}/{env.config.id}/common/fields", json={"section_id": section_id(body, "basics"), "field": {"key": "title", "label": "T2", "type": "short_text"}}), "KEY_RESERVED")):
        refused = call()
        assert refused.status_code == 409 and refused.json()["code"] == code, (code, refused.text)
    ok = env.client.patch(f"{BASE}/{env.config.id}/common/fields/title", json={"patch": {"label": "Event name", "help_text": "Short and clear", "placeholder": "e.g. Summit"}})
    assert ok.status_code == 200 and fields_of(ok.json())["title"]["label"] == "Event name" and fields_of(ok.json())["title"]["system"] is True
    row = stored(env)["title"]
    assert row["source"] == "core" and row["core_key"] == "title" and row["renderer"] == "text" and row["label"] == "Event name"                  # still a built-in field, nothing else changed
    off = env.client.patch(f"{BASE}/{env.config.id}/common/fields/tags", json={"patch": {"enabled": False, "required": True}})
    assert off.status_code == 200 and fields_of(off.json())["tags"]["enabled"] is False and fields_of(off.json())["tags"]["required"] is False
    assert stored(env)["tags"]["is_enabled"] is False                                                                                    # switched off, and kept in the draft
    dup = env.client.post(f"{BASE}/{env.config.id}/common/fields", json={"section_id": section_id(body, "extra"), "field": {"key": "audience", "label": "Another", "type": "short_text"}})
    assert dup.status_code == 409 and dup.json()["code"] == "DUPLICATE_KEY"
    label = env.client.post(f"{BASE}/{env.config.id}/common/fields", json={"section_id": section_id(body, "extra"), "field": {"key": "other_one", "label": "audience", "type": "short_text"}})
    assert label.status_code == 409 and label.json()["code"] == "DUPLICATE_LABEL"


def test_a_stale_etag_is_refused_every_change_is_audited_and_a_published_form_forks_a_draft(env):
    first = common(env).json()
    assert env.client.post(f"{BASE}/{env.config.id}/common/sections", json={"title": "One", "base_etag": first["etag"]}).status_code == 201
    stale = env.client.post(f"{BASE}/{env.config.id}/common/sections", json={"title": "Two", "base_etag": first["etag"]})
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT" and stale.json()["current_etag"] != first["etag"]
    assert env.client.post(f"{BASE}/{env.config.id}/common/sections", json={"title": "Two"}).status_code == 201                          # no etag: allowed
    actions = [a.action for a in env.db.query(EventFormAudit).filter_by(configuration_id=env.config.id).all()]
    assert actions.count("common_section_added") == 2
    assert env.client.get(f"{BASE}/{env.config.id}/audit").status_code == 200

    env.draft.status, env.config.status, env.draft.published_at = "published", "published", datetime.utcnow()
    env.db.commit()
    forked = env.client.post(f"{BASE}/{env.config.id}/common/sections", json={"title": "Three"})
    assert forked.status_code == 201 and forked.json()["version"] == 2 and forked.json()["version_status"] == "draft"
    env.db.refresh(env.draft)
    assert env.draft.status == "published" and len(env.draft.sections) == 4                                                              # the published version is untouched
    assert env.client.post(f"{BASE}/{uuid4()}/common/sections", json={"title": "X"}).status_code == 404


# --- values ----------------------------------------------------------------------------------------------------------------------------

def sections_with_types():
    return [{"id": str(uuid4()), "stable_key": "s", "label": "S", "position": 1, "is_enabled": True, "fields": [
        {**custom("bio", 1, "rich_text_editor"), "field_type": "rich_text", "value_type": "string"},
        {**custom("kickoff", 2, "datetime"), "field_type": "datetime", "validation": {"min": "2026-01-01T00:00"}},
        {**custom("catering", 3, "checkbox"), "field_type": "toggle", "value_type": "boolean"},
        {**custom("intro", 4, "video_input"), "field_type": "video", "validation": {"max_items": 1}},
        {**custom("level", 5, "radio_group"), "field_type": "radio", "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}], "required": True},
        {**custom("secret", 6, "text_input"), "field_type": "short_text", "visibility": "enterprise_admin_only"},
        custom("legacy", 7, "text", validation={"max_length": 5})]}]


def test_values_of_the_new_types_are_checked_and_cleaned_while_older_fields_keep_their_older_checks():
    sections = sections_with_types()
    ids = {f["stable_key"]: f["id"] for f in sections[0]["fields"]}
    good = [{"field_id": ids["bio"], "value": "<p>Hi</p><script>x()</script>"}, {"field_id": ids["kickoff"], "value": "2026-06-01T09:30:00Z"}, {"field_id": ids["catering"], "value": True},
            {"field_id": ids["intro"], "value": ["https://youtu.be/dQw4w9WgXcQ"]}, {"field_id": ids["level"], "value": "a"}, {"field_id": ids["secret"], "value": "note"},
            {"field_id": ids["legacy"], "value": "abc"}]
    result = event_svc.validate_custom_values(good, sections)
    by_id = {r["field_id"]: r["value"] for r in result}
    assert by_id[ids["bio"]] == "<p>Hi</p>" and by_id[ids["kickoff"]] == "2026-06-01T09:30:00" and by_id[ids["catering"]] is True and by_id[ids["legacy"]] == "abc"
    assert by_id[ids["intro"]][0]["provider"] == "youtube" and by_id[ids["intro"]][0]["video_id"] == "dQw4w9WgXcQ"

    for bad, why in (({"field_id": ids["kickoff"], "value": "tomorrow"}, "date and time"), ({"field_id": ids["kickoff"], "value": "2025-01-01T00:00"}, "cannot be before"),
                     ({"field_id": ids["catering"], "value": "yes"}, "true or false"), ({"field_id": ids["intro"], "value": ["javascript:alert(1)"]}, "http"),
                     ({"field_id": ids["level"], "value": "z"}, "pick one"), ({"field_id": ids["legacy"], "value": "too long for the rule"}, "too long")):
        others = [{"field_id": ids["level"], "value": "a"}] if bad["field_id"] != ids["level"] else []
        with pytest.raises(HTTPException) as caught:
            event_svc.validate_custom_values([bad, *others], sections)
        assert caught.value.status_code == 400 and why in str(caught.value.detail), (bad, caught.value.detail)
    with pytest.raises(HTTPException) as missing:
        event_svc.validate_custom_values([{"field_id": ids["legacy"], "value": "abc"}], sections)
    assert "Required custom field missing" in str(missing.value.detail)

    public, private = write.split_private(result, sections)
    assert {r["field_id"] for r in private} == {ids["secret"]} and ids["secret"] not in {r["field_id"] for r in public} and len(public) == 6
    kept_public, kept_private = write.split_private([{"field_id": ids["bio"], "value": "<p>New</p>"}], sections, private)
    assert [r["value"] for r in kept_private] == ["note"] and len(kept_public) == 1                                                     # a private value not sent is kept
    _, cleared = write.split_private([{"field_id": ids["secret"], "value": None}], sections, private)
    assert cleared == []                                                                                                                # sending null clears it
    _, replaced = write.split_private([{"field_id": ids["secret"], "value": "new"}], sections, private)
    assert [r["value"] for r in replaced] == ["new"]


def test_enterprise_only_values_are_kept_out_of_the_public_column_and_the_public_view(env):
    tenant = uuid4()
    sections = sections_with_types()
    version = EventFormConfigurationVersion(configuration_id=env.config.id, version=7, status="published", sections=sections, published_at=datetime.utcnow())
    env.db.add(version)
    env.db.commit()
    ids = {f["stable_key"]: f["id"] for f in sections[0]["fields"]}
    enterprise = make_enterprise(env.db, tenant, "Apollo Events")
    values = [{"field_id": ids["level"], "value": "a"}, {"field_id": ids["secret"], "value": "Call the venue first"}, {"field_id": ids["legacy"], "value": "abc"}]
    public, private = write.split_private(event_svc.validate_custom_values(values, sections), sections)
    event = make_event(env.db, tenant, enterprise, title="Summit", status="published", form_configuration_version_id=version.id, custom_values=public, private_custom_values=private)

    assert "Call the venue" not in str(event.custom_values) and event.private_custom_values[0]["value"] == "Call the venue first"
    anyone = client_for(env.db, None).get(f"{API}/{event.id}/form/common").json()
    assert {v["key"] for v in anyone["custom_fields"]} == {"level", "legacy"} and "Call the venue" not in str(anyone) and "secret" not in str(anyone["definition"])
    root = client_for(env.db, env.root).get(f"{API}/{event.id}/form/common").json()
    assert {v["key"] for v in root["custom_fields"]} == {"level", "legacy", "secret"} and "Call the venue" in str(root)
    detail = client_for(env.db, None).get(f"{API}/{event.id}")
    assert detail.status_code == 200 and "Call the venue first" not in detail.text                                                         # the event's own public answer never holds it


# --- review, submit, publish ----------------------------------------------------------------------------------------------------------

def test_review_lists_problems_submit_is_a_checkpoint_and_publishing_keeps_the_new_settings(env):
    whole = copy.deepcopy(env.draft.sections)
    env.draft.sections = [{**s, "fields": [f for f in s["fields"] if f.get("core_key") != "start_date"]} for s in whole]      # a draft that lost a required built-in field
    env.db.commit()
    review = env.client.get(f"{BASE}/{env.config.id}/common/review").json()
    assert review["ready"] is False and any("start_date" in p["message"] for p in review["problems"]) and review["review_status"] == "draft"
    assert review["changes"]["since_version"] is None and "speaker" in review["changes"]["added"]
    refused = env.client.post(f"{BASE}/{env.config.id}/common/submit")
    assert refused.status_code == 422 and refused.json()["code"] == "FORM_NOT_READY" and refused.json()["errors"]

    env.draft.sections = [{**s, "fields": s["fields"]} for s in SECTIONS[:2]]
    env.db.commit()
    body = common(env).json()
    new_field = env.client.post(f"{BASE}/{env.config.id}/common/fields", json={"section_id": section_id(body, "extra"), "field": {
        "key": "kickoff", "label": "Kick-off", "type": "datetime", "visibility": "enterprise_admin_only"}})
    assert new_field.status_code == 201
    off = env.client.patch(f"{BASE}/{env.config.id}/common/fields/old_field", json={"patch": {"label": "Old field"}})
    assert off.status_code == 200

    ready = env.client.get(f"{BASE}/{env.config.id}/common/review").json()
    assert ready["ready"] is True and ready["problems"] == [] and ready["field_count"] >= 18
    sent = env.client.post(f"{BASE}/{env.config.id}/common/submit", json={"base_etag": ready["etag"]})
    assert sent.status_code == 200 and sent.json()["review_status"] == "submitted" and common(env).json()["review_status"] == "submitted"
    env.client.post(f"{BASE}/{env.config.id}/common/sections", json={"title": "After"})
    assert common(env).json()["review_status"] == "draft"                                                                                 # an edit withdraws it

    settings.FORM_REQUIRE_REVIEW = True
    try:
        blocked = env.client.post(f"{BASE}/{env.config.id}/publish")
        assert blocked.status_code == 409 and blocked.json()["code"] == "REVIEW_REQUIRED"
        env.client.post(f"{BASE}/{env.config.id}/common/submit")
        published = env.client.post(f"{BASE}/{env.config.id}/publish")
        assert published.status_code == 200, published.text
    finally:
        settings.FORM_REQUIRE_REVIEW = False
    env.db.refresh(env.draft)
    fields = {f.get("stable_key") or f.get("core_key"): f for s in env.draft.sections for f in s["fields"]}
    assert fields["kickoff"]["field_type"] == "datetime" and fields["kickoff"]["visibility"] == "enterprise_admin_only"                    # preserved by the publish normalisation
    assert "old_field" not in fields                                                                                                      # a switched-off field is not in a published version
    active = event_svc.build_active_response(env.config, env.draft)
    assert any(f.get("field_type") == "datetime" for s in active["sections"] for f in s["fields"])


# --- trainings -------------------------------------------------------------------------------------------------------------------------

def test_the_same_builder_works_for_a_training_form(env):
    bind = env.db.get_bind()
    from app.models.training_form_config_model import TrainingFormAssignment, TrainingFormAudit

    for model in (TrainingFormConfiguration, TrainingFormConfigurationVersion, TrainingFormAudit, TrainingFormAssignment):
        model.__table__.create(bind=bind, checkfirst=True)
    config = TrainingFormConfiguration(name="Course form", scope="global", status="draft", is_active=False, current_version=1)
    env.db.add(config)
    env.db.flush()
    sections = [{"id": "s1", "stable_key": "main", "label": "Main", "position": 1, "is_enabled": True, "fields": [
        {"id": "f1", "source": "core", "core_key": "title", "label": "Title", "renderer": "text", "value_type": "string", "required": True, "is_enabled": True, "position": 1, "options": [], "validation": {}},
        {"id": "f2", "source": "core", "core_key": "category", "label": "Category", "renderer": "text", "value_type": "string", "required": True, "is_enabled": True, "position": 2, "options": [], "validation": {}},
        {"id": "f3", "source": "core", "core_key": "promotional_video", "label": "Promo video", "renderer": "url", "value_type": "string", "required": False, "is_enabled": True, "position": 3, "options": [], "validation": {}},
        {"id": "f4", "source": "custom", "stable_key": "clip", "label": "Clip", "renderer": "video", "value_type": "string", "required": False, "is_enabled": True, "position": 4, "options": [], "validation": {"allowed_providers": ["youtube"]}}]}]
    draft = TrainingFormConfigurationVersion(configuration_id=config.id, version=1, status="draft", sections=sections)
    env.db.add(draft)
    env.db.commit()

    base = f"/api/v1/trainings/form-configuration/admin/{config.id}/common"
    view = env.client.get(base).json()
    got = {f["key"]: f for s in view["definition"]["sections"] for f in s["fields"]}
    assert got["title"]["system"] is True and got["category"]["system"] is True and got["promotional_video"]["type"] == "video" and got["clip"]["type"] == "video"
    assert got["clip"]["validation"] == {"allowed_providers": ["youtube"]}

    added = env.client.post(f"{base}/fields", json={"section_id": "s1", "field": {"key": "level_note", "label": "Level note", "type": "rich_text", "visibility": "enterprise_admin_only"}})
    assert added.status_code == 201, added.text
    assert {"level_note"} <= {f["key"] for s in added.json()["definition"]["sections"] for f in s["fields"]}
    assert env.client.delete(f"{base}/fields/title").json()["code"] == "PROTECTED_FIELD"
    assert env.client.delete(f"{base}/fields/promotional_video").json()["code"] == "CORE_FIELD"
    assert env.client.patch(f"{base}/fields/clip", json={"patch": {"label": "Intro clip", "enabled": False}}).status_code == 200
    env.db.refresh(draft)
    rows = {f.get("stable_key") or f.get("core_key"): f for s in draft.sections for f in s["fields"]}
    assert rows["level_note"]["field_type"] == "rich_text" and rows["level_note"]["visibility"] == "enterprise_admin_only" and rows["clip"]["is_enabled"] is False
    assert rows["clip"]["renderer"] == "video" and rows["title"]["core_key"] == "title"
    review = env.client.get(f"{base}/review").json()
    assert review["ready"] is True and review["version"] == 1
    assert env.client.post(f"{base}/submit").json()["review_status"] == "submitted"
