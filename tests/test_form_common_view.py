"""Event and Training forms in the common form contract: the type mapping, protected fields, the three audiences, and the event's own form and custom values."""
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.form_builder import legacy_sections as legacy
from app.form_builder import projection
from app.models.event_form_config_model import EventFormAssignment, EventFormConfiguration, EventFormConfigurationVersion
from app.services.event_form_registry import DOMAIN_REQUIRED_CORE_KEYS as EVENT_REQUIRED, REGISTRY_BY_KEY as EVENT_REGISTRY
from app.services.training_form_registry import DOMAIN_REQUIRED_CORE_KEYS as TRAINING_REQUIRED, REGISTRY_BY_KEY as TRAINING_REGISTRY
from event_sql_support import client_for, make_enterprise, make_event, make_session, reset_overrides, staff_user, super_admin_user

API = "/api/v1/events"


def core(key, position, **over):
    return {"id": str(uuid4()), "source": "core", "core_key": key, "label": key.replace("_", " ").title(), "renderer": EVENT_REGISTRY[key]["default_renderer"],
            "value_type": EVENT_REGISTRY[key]["value_type"], "required": key in EVENT_REQUIRED, "is_enabled": True, "position": position, "options": [], "validation": {}, **over}


def custom(stable_key, position, renderer, **over):
    return {"id": str(uuid4()), "source": "custom", "stable_key": stable_key, "label": over.pop("label", stable_key.replace("_", " ").title()), "renderer": renderer,
            "value_type": "string", "required": False, "is_enabled": True, "position": position, "options": [], "validation": {}, **over}


SECTIONS = [
    {"id": str(uuid4()), "stable_key": "basics", "label": "Basics", "position": 1, "is_enabled": True, "fields": [
        core("title", 1), core("description", 2), core("category", 3), core("start_date", 4), core("end_date", 5), core("pricing_type", 6), core("tags", 7),
        core("primary_image", 8, is_enabled=False)]},
    {"id": str(uuid4()), "stable_key": "extra", "label": "Extra", "position": 2, "is_enabled": True, "fields": [
        custom("speaker", 1, "text", validation={"max_length": 60}, required=True),
        custom("agenda_note", 2, "textarea"),
        custom("audience", 3, "select", options=[{"value": "kids", "label": "Kids"}, {"value": "adults", "label": "Adults"}]),
        custom("topics", 4, "multi_select", options=[{"value": "a", "label": "A"}], value_type="string[]"),
        custom("parking", 5, "checkbox", value_type="boolean"),
        custom("starts", 6, "datetime"), custom("link", 7, "url"), custom("seats", 8, "number", value_type="number"),
        custom("staff_only", 9, "text", visibility="enterprise_admin_only"),
        custom("old_field", 10, "text", is_enabled=False)]},
    {"id": str(uuid4()), "stable_key": "hidden_section", "label": "Hidden", "position": 3, "is_enabled": False, "fields": [custom("in_hidden", 1, "text")]},
]


# --- the mapping ---------------------------------------------------------------------------------------------------------------

def test_event_fields_get_explicit_types_protected_flags_and_visibility():
    definition = legacy.to_common(SECTIONS, registry=EVENT_REGISTRY, domain_required=EVENT_REQUIRED)
    fields = {f["key"]: f for s in definition["sections"] for f in s["fields"]}
    assert {k: fields[k]["type"] for k in ("title", "description", "category", "start_date", "pricing_type", "tags", "primary_image")} == {
        "title": "short_text", "description": "long_text", "category": "dropdown", "start_date": "datetime", "pricing_type": "dropdown", "tags": "tags", "primary_image": "additional_images"}
    assert {k: fields[k]["type"] for k in ("speaker", "agenda_note", "audience", "topics", "parking", "starts", "link", "seats")} == {
        "speaker": "short_text", "agenda_note": "long_text", "audience": "dropdown", "topics": "checkbox", "parking": "toggle", "starts": "datetime", "link": "reference_url", "seats": "number"}
    assert fields["starts"]["module_type"] is True and fields["starts"]["renderer"] == "datetime_picker" and "module_type" not in fields["speaker"] and fields["speaker"]["renderer"] == "text_input"
    assert fields["audience"]["options"] == [{"value": "kids", "label": "Kids"}, {"value": "adults", "label": "Adults"}] and fields["audience"]["renderer"] == "select"
    assert {k for k, f in fields.items() if f["system"]} == set(EVENT_REQUIRED) and fields["category"]["options_source"] == "event_categories"
    assert fields["staff_only"]["visibility"] == "enterprise_admin_only" and fields["speaker"]["visibility"] == "published" and fields["primary_image"]["validation"] == {"max_items": 1}
    assert fields["old_field"]["enabled"] is False and fields["speaker"]["kind"] == "custom" and fields["title"]["kind"] == "core" and fields["title"]["core_key"] == "title"
    assert [s["enabled"] for s in definition["sections"]] == [True, True, False] and fields["speaker"]["order"] == 1
    # nothing like text, textarea or select is returned as a type
    assert not ({f["type"] for f in fields.values()} & {"text", "textarea", "select", "multi_select", "url"})


def test_training_fields_map_by_key_and_renderer():
    sections = [{"id": "s1", "stable_key": "main", "label": "Main", "position": 1, "is_enabled": True, "fields": [
        {"id": "f1", "source": "core", "core_key": "title", "renderer": "text", "position": 1, "is_enabled": True, "required": True},
        {"id": "f2", "source": "core", "core_key": "promotional_video", "renderer": "url", "position": 2, "is_enabled": True},
        {"id": "f3", "source": "core", "core_key": "gallery_images", "renderer": "url", "position": 3, "is_enabled": True},
        {"id": "f4", "source": "core", "core_key": "start_time", "renderer": "time", "position": 4, "is_enabled": True},
        {"id": "f5", "source": "core", "core_key": "requires_approval", "renderer": "checkbox", "position": 5, "is_enabled": True},
        {"id": "f6", "source": "core", "core_key": "release_rule", "renderer": "json", "position": 6, "is_enabled": True},
        {"id": "f7", "source": "custom", "stable_key": "clip", "renderer": "video", "label": "Clip", "position": 7, "is_enabled": True},
        {"id": "f8", "source": "custom", "stable_key": "handout", "renderer": "document", "label": "Handout", "position": 8, "is_enabled": True},
        {"id": "f9", "source": "custom", "stable_key": "photo", "renderer": "image", "label": "Photo", "position": 9, "is_enabled": True}]}]
    fields = {f["key"]: f for s in legacy.to_common(sections, registry=TRAINING_REGISTRY, domain_required=TRAINING_REQUIRED)["sections"] for f in s["fields"]}
    assert {k: v["type"] for k, v in fields.items()} == {"title": "short_text", "promotional_video": "video", "gallery_images": "additional_images", "start_time": "time",
                                                         "requires_approval": "toggle", "release_rule": "json", "clip": "video", "handout": "supporting_document", "photo": "additional_images"}
    assert {k for k, f in fields.items() if f["system"]} == {"title"} and TRAINING_REQUIRED == {"title", "category"}
    assert fields["promotional_video"]["validation"] == {"max_items": 1} and fields["release_rule"]["module_type"] is True


def test_the_three_audiences_see_what_they_may():
    definition = legacy.to_common(SECTIONS, registry=EVENT_REGISTRY, domain_required=EVENT_REQUIRED)
    keys = lambda audience: [f["key"] for s in projection.project_form(definition, audience)["sections"] for f in s["fields"]]   # noqa: E731
    assert "staff_only" not in keys("public") and "old_field" not in keys("public") and "primary_image" not in keys("public") and "in_hidden" not in keys("public")
    assert "staff_only" in keys("enterprise_admin") and "old_field" not in keys("enterprise_admin") and "in_hidden" not in keys("enterprise_admin")
    assert {"staff_only", "old_field", "primary_image", "in_hidden"} <= set(keys("super_admin"))
    sample = projection.project_form(definition, "enterprise_admin")["sections"][0]["fields"][0]
    assert sample["protected"] is True and sample["core_key"] == "title" and "legacy_renderer" not in sample
    public = projection.project_form(definition, "public")
    assert [s["key"] for s in public["sections"]] == ["basics", "extra"]
    assert set(next(f for s in public["sections"] for f in s["fields"] if f["key"] == "starts")) == {"id", "key", "label", "type", "renderer", "order", "options", "module_type"}


def test_custom_values_follow_the_same_filter():
    definition = legacy.to_common(SECTIONS, registry=EVENT_REGISTRY, domain_required=EVENT_REQUIRED)
    ids = {f["key"]: f["id"] for s in definition["sections"] for f in s["fields"]}
    stored = [{"field_id": ids["speaker"], "value": "Dr Rao"}, {"field_id": ids["staff_only"], "value": "Do not show"}, {"field_id": ids["old_field"], "value": "gone"},
              {"field_id": ids["parking"], "value": True}, {"field_id": "unknown-id", "value": "x"}]
    by_key = legacy.values_by_key(definition, stored)
    assert by_key == {"speaker": "Dr Rao", "staff_only": "Do not show", "old_field": "gone", "parking": True}
    assert legacy.values_by_key(definition, {"speaker": "A", "nope": 1}) == {"speaker": "A"}
    public = projection.project_values(definition, by_key, "public")
    assert [(v["key"], v["type"]) for v in public] == [("speaker", "short_text"), ("parking", "toggle")] and "Do not show" not in str(public) and "gone" not in str(public)
    assert [v["key"] for v in projection.project_values(definition, by_key, "enterprise_admin")] == ["speaker", "parking", "staff_only"]
    assert "old_field" in [v["key"] for v in projection.project_values(definition, by_key, "super_admin")]


# --- through the API --------------------------------------------------------------------------------------------------------------

@pytest.fixture
def env(monkeypatch):
    for target in ("app.services.super_admin_identity.fetch_internal_user_by_id", "app.services.super_admin_identity.fetch_auth_me_profile",
                   "app.services.invigorate_auth_client.fetch_tenant_me_profile", "app.services.invigorate_auth_client.fetch_auth_me_profile"):
        monkeypatch.setattr(target, lambda *a, **k: None)
    db = make_session()
    EventFormAssignment.__table__.create(bind=db.get_bind(), checkfirst=True)
    tenant = uuid4()
    enterprise = make_enterprise(db, tenant, "Apollo Events")
    config = EventFormConfiguration(name="Summit form", scope="global", status="published", is_active=True, current_version=1)
    db.add(config)
    db.flush()
    version = EventFormConfigurationVersion(configuration_id=config.id, version=1, status="published", sections=SECTIONS, published_at=datetime.utcnow())
    db.add(version)
    db.flush()
    draft = EventFormConfigurationVersion(configuration_id=config.id, version=2, status="draft", sections=SECTIONS[:1])
    db.add(draft)
    db.commit()
    definition = legacy.to_common(SECTIONS, registry=EVENT_REGISTRY, domain_required=EVENT_REQUIRED)
    ids = {f["key"]: f["id"] for s in definition["sections"] for f in s["fields"]}
    event = make_event(db, tenant, enterprise, title="Summit", status="published", form_configuration_version_id=version.id,
                       custom_values=[{"field_id": ids["speaker"], "value": "Dr Rao"}, {"field_id": ids["staff_only"], "value": "Do not show"}, {"field_id": ids["old_field"], "value": "gone"}])
    yield SimpleNamespace(db=db, tenant=tenant, event=event, config=config, version=version, draft=draft, owner=staff_user(tenant, "admin", "admin@apollo.test"),
                          other=staff_user(uuid4(), "admin", "x@other.test"), root=super_admin_user(), monkeypatch=monkeypatch)
    reset_overrides()
    db.close()


def test_an_event_shows_its_own_form_and_values_by_who_is_asking(env):
    url = f"{API}/{env.event.id}/form/common"
    public = client_for(env.db, None).get(url)
    assert public.status_code == 200
    body = public.json()
    assert body["audience"] == "public" and body["event_id"] == str(env.event.id) and body["version"] == 1 and body["configuration_id"] == str(env.config.id)
    assert [(v["key"], v["value"]) for v in body["custom_fields"]] == [("speaker", "Dr Rao")] and "Do not show" not in str(body) and "visibility" not in str(body["custom_fields"])
    keys = [f["key"] for s in body["definition"]["sections"] for f in s["fields"]]
    assert "staff_only" not in keys and "old_field" not in keys and "title" in keys

    owner = client_for(env.db, env.owner).get(url).json()
    assert owner["audience"] == "enterprise_admin" and [v["key"] for v in owner["custom_fields"]] == ["speaker", "staff_only"] and owner["custom_fields"][1]["visibility"] == "enterprise_admin_only"
    root = client_for(env.db, env.root).get(url).json()
    assert root["audience"] == "super_admin" and {v["key"] for v in root["custom_fields"]} == {"speaker", "staff_only", "old_field"}
    stranger = client_for(env.db, env.other).get(url).json()
    assert stranger["audience"] == "public" and "staff_only" not in str(stranger)                                   # another business's admin is just a reader

    env.event.status = "draft"
    env.db.commit()
    assert client_for(env.db, None).get(url).status_code == 404 and client_for(env.db, env.owner).get(url).status_code == 200
    assert client_for(env.db, None).get(f"{API}/{uuid4()}/form/common").status_code == 404


def test_the_builder_sees_the_draft_and_everything_and_the_enterprise_admin_the_active_form(env):
    base = f"{API}/form-configuration/admin/{env.config.id}/common"
    builder = client_for(env.db, env.root).get(base)
    assert builder.status_code == 200
    body = builder.json()
    assert body["audience"] == "super_admin" and body["version"] == 2 and body["version_status"] == "draft" and body["name"] == "Summit form" and body["is_active"] is True
    assert [s["key"] for s in body["definition"]["sections"]] == ["basics"] and body["field_count"] == 8
    published = client_for(env.db, env.root).get(base, params={"version_id": str(env.version.id)}).json()
    assert published["version"] == 1 and {"old_field", "in_hidden"} <= {f["key"] for s in published["definition"]["sections"] for f in s["fields"]}      # the builder sees switched-off ones
    assert client_for(env.db, env.root).get(base, params={"version_id": str(uuid4())}).status_code == 404
    assert client_for(env.db, env.root).get(f"{API}/form-configuration/admin/{uuid4()}/common").status_code == 404
    assert client_for(env.db, staff_user(env.tenant, "customer", "c@x.test") | {"role": "customer"}).get(base).status_code == 403
    assert client_for(env.db, None).get(base).status_code == 401

    from app.services import event_form_config_service as svc
    env.monkeypatch.setattr(svc, "get_active_form_configuration_service", lambda db, user, access_token=None: svc.build_active_response(env.config, env.version))
    active = client_for(env.db, env.owner).get(f"{API}/form-configuration/active/common")
    assert active.status_code == 200
    view = active.json()
    keys = [f["key"] for s in view["definition"]["sections"] for f in s["fields"]]
    assert view["audience"] == "enterprise_admin" and view["version"] == 1 and "staff_only" in keys and "old_field" not in keys and "in_hidden" not in keys
    speaker = next(f for s in view["definition"]["sections"] for f in s["fields"] if f["key"] == "speaker")
    assert speaker["required"] is True and speaker["validation"] == {"max_length": 60} and speaker["type"] == "short_text" and speaker["renderer"] == "text_input"
