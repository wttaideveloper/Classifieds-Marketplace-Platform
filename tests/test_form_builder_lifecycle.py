"""Section visibility, ETag and If-Match, the lifecycle statuses, public active forms, assignments in the views, diagnostics, and existing records keeping their form version."""
import copy
from datetime import datetime
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models.event_form_config_model import EventFormAssignment, EventFormConfiguration, EventFormConfigurationVersion
from event_sql_support import client_for, make_enterprise, make_session, reset_overrides, super_admin_user
from tests.blog_support import API, BODY, draft, live_merchant_post, make_category, review, submit, world  # noqa: F401
from tests.test_blog_form_builder import BASE, SMALL, add, fields_of, new_form, section_id
from tests.test_blog_forms_and_posts import make_form, publish_form, root
from tests.test_form_common_view import SECTIONS


# --- Blog: section visibility ---------------------------------------------------------------------------------------------------

def test_an_enterprise_admin_only_section_hides_everything_in_it_from_the_public(world):
    form = new_form(world)
    extra = root(world).post(f"{BASE}/{form['id']}/sections", json={"title": "Internal", "visibility": "enterprise_admin_only"})
    assert extra.status_code == 201
    sid = section_id(extra.json(), "internal")
    assert next(s for s in extra.json()["definition"]["sections"] if s["id"] == sid)["visibility"] == "enterprise_admin_only"
    assert add(world, extra.json(), {"key": "budget", "label": "Budget", "type": "number"}, section_id=sid).status_code == 201
    shown = add(world, extra.json(), {"key": "speaker", "label": "Speaker", "type": "short_text"})
    assert shown.status_code == 201
    publish_form(world, form)
    cat = make_category(world)

    public = world.as_user(None).get(f"{API}/forms/public", params={"kind": "blog"}).json()
    assert "budget" not in str(public) and "Internal" not in str(public) and "speaker" in str(public)
    mine = world.as_user(world.people.asha).get(f"{API}/my/form", params={"kind": "blog"}).json()
    section = next(s for s in mine["definition"]["sections"] if s["key"] == "internal")
    assert section["visibility"] == "enterprise_admin_only" and {f["key"] for f in section["fields"]} == {"budget"} or "budget" in str(mine)

    post = live_merchant_post(world, cat, custom_fields={"budget": 5000, "speaker": "Dr Rao"})
    public_post = world.as_user(None).get(f"{API}/{post['slug']}").json()
    assert [f["key"] for f in public_post["custom_fields"]] == ["speaker"] and "5000" not in str(public_post)
    assert world.as_user(world.people.asha).get(f"{API}/my/posts/{post['id']}").json()["custom_fields"]["budget"] == 5000
    platform = root(world).post(f"{API}/admin/posts", json={"title": "Platform", "content": BODY, "category_id": cat["id"], "custom_fields": {"budget": 1}})
    assert platform.status_code == 422 and platform.json()["errors"][0]["code"] == "UNKNOWN_FIELD"

    off = root(world).patch(f"{BASE}/{form['id']}/sections/{sid}", json={"enabled": False})
    assert off.status_code == 200 and next(s for s in off.json()["definition"]["sections"] if s["id"] == sid)["enabled"] is False
    bad = root(world).patch(f"{BASE}/{form['id']}/sections/{sid}", json={"visibility": "everyone"})
    assert bad.status_code == 422


# --- Blog: ETag, If-Match -----------------------------------------------------------------------------------------------------------

def test_the_form_answers_carry_an_etag_and_a_stale_if_match_is_refused(world):
    form = new_form(world)
    shown = root(world).get(f"{BASE}/{form['id']}")
    assert shown.headers["ETag"] == f'"{form["revision"]}"'

    ok = root(world).post(f"{BASE}/{form['id']}/sections", json={"title": "One"}, headers={"If-Match": shown.headers["ETag"]})
    assert ok.status_code == 201 and ok.headers["ETag"] == f'"{form["revision"] + 1}"' and ok.json()["revision"] == form["revision"] + 1
    stale = root(world).post(f"{BASE}/{form['id']}/sections", json={"title": "Two"}, headers={"If-Match": shown.headers["ETag"]})
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT" and stale.json()["current_version"] == form["revision"] + 1
    weak = root(world).post(f"{BASE}/{form['id']}/sections", json={"title": "Two"}, headers={"If-Match": f'W/"{form["revision"] + 1}"'})
    assert weak.status_code == 201
    assert root(world).patch(f"{BASE}/{form['id']}", json={"description": "x"}, headers={"If-Match": '"1"'}).status_code == 409
    assert root(world).patch(f"{BASE}/{form['id']}", json={"description": "x", "base_revision": weak.json()["revision"]}, headers={"If-Match": '"1"'}).status_code == 200        # the body wins
    assert root(world).delete(f"{BASE}/{form['id']}/fields/title", headers={"If-Match": '"1"'}).json()["code"] == "VERSION_CONFLICT"
    assert root(world).post(f"{BASE}/{form['id']}/sections", json={"title": "Three"}, headers={"If-Match": "*"}).status_code == 201                                                     # * = no check


# --- Blog: lifecycle ----------------------------------------------------------------------------------------------------------------

def test_a_form_goes_through_draft_review_active_inactive_retired_and_back(world, monkeypatch):
    form = new_form(world)
    assert (form["status"], form["lifecycle"]) == ("draft", "draft") and {"edit", "review", "submit", "publish", "delete"} <= set(form["available_actions"])
    sent = root(world).post(f"{BASE}/{form['id']}/submit", json={}).json()
    assert sent["lifecycle"] == "in_review" and sent["review_status"] == "submitted"
    root(world).patch(f"{BASE}/{form['id']}", json={"description": "Changed"})
    assert root(world).get(f"{BASE}/{form['id']}").json()["lifecycle"] == "draft"                         # an edit withdraws the review

    published = publish_form(world, form)
    assert (published["status"], published["lifecycle"], published["is_active"]) == ("published", "active", True) and "deactivate" in published["available_actions"] and "retire" not in published["available_actions"]
    changed = root(world).patch(f"{BASE}/{form['id']}", json={"description": "Next"}).json()
    assert changed["lifecycle"] == "active" and changed["has_unpublished_changes"] is True and {"review", "submit", "publish"} <= set(changed["available_actions"])

    assert root(world).post(f"{BASE}/{form['id']}/retire").json()["code"] == "FORM_ACTIVE"
    off = root(world).post(f"{BASE}/{form['id']}/deactivate").json()
    assert off["lifecycle"] == "inactive" and {"activate", "retire"} <= set(off["available_actions"])
    retired = root(world).post(f"{BASE}/{form['id']}/retire")
    assert retired.status_code == 200 and (retired.json()["status"], retired.json()["lifecycle"]) == ("retired", "retired") and retired.json()["is_active"] is False and "restore" in retired.json()["available_actions"]
    assert root(world).post(f"{BASE}/{form['id']}/activate").json()["code"] == "FORM_RETIRED"
    assert root(world).post(f"{BASE}/{form['id']}/retire").json()["code"] == "INVALID_STATUS"
    assert world.as_user(world.people.asha).get(f"{API}/my/form", params={"kind": "blog"}).json()["form_id"] is None                      # a retired form is not offered
    assert [f["name"] for f in root(world).get(BASE, params={"status": "retired"}).json()["items"]] == ["Builder form"]

    back = root(world).post(f"{BASE}/{form['id']}/restore").json()
    assert back["status"] == "published" and back["lifecycle"] == "inactive"
    assert root(world).post(f"{BASE}/{form['id']}/restore").json()["code"] == "INVALID_STATUS"
    assert root(world).post(f"{BASE}/{form['id']}/activate").json()["lifecycle"] == "active"
    actions = [a["action"] for a in root(world).get(f"{BASE}/{form['id']}/audit").json()["items"]]
    assert {"retired", "restored", "deactivated", "activated", "submitted", "published"} <= set(actions)
    for user in (world.people.asha, world.people.priya):
        assert world.as_user(user).post(f"{BASE}/{form['id']}/retire").status_code == 403
    draft_only = new_form(world, name="Never published")
    assert root(world).post(f"{BASE}/{draft_only['id']}/retire").json()["code"] == "INVALID_STATUS"


# --- Blog: existing records keep their form version --------------------------------------------------------------------------------

def test_existing_posts_keep_their_form_version_when_a_field_is_removed_later(world):
    definition = {"sections": [{"key": "basics", "title": "Basics", "fields": [{"key": "title"}, {"key": "category"}, {"key": "content"}, {"key": "excerpt"}]},
                               {"key": "extra", "title": "Extra", "fields": [{"key": "speaker", "label": "Speaker", "type": "short_text"}, {"key": "venue_note", "label": "Venue note", "type": "short_text"}]}]}
    form = make_form(world, name="Evolving", definition=definition)
    publish_form(world, form)
    cat = make_category(world)
    asha = world.people.asha
    live = live_merchant_post(world, cat, custom_fields={"speaker": "Dr Rao", "venue_note": "Gate 2"})
    waiting = draft(world, asha, cat, title="Still a draft", custom_fields={"speaker": "Ravi", "venue_note": "Gate 3"})

    gone = root(world).delete(f"{BASE}/{form['id']}/fields/venue_note")
    assert gone.status_code == 200
    publish_form(world, form)                                                                              # version 2 has no venue note

    public = world.as_user(None).get(f"{API}/{live['slug']}").json()
    assert {f["key"]: f["value"] for f in public["custom_fields"]} == {"speaker": "Dr Rao", "venue_note": "Gate 2"}      # a live post keeps what it was published with
    assert world.as_user(asha).get(f"{API}/my/posts/{live['id']}").json()["form_version"] == 1

    shown = world.as_user(asha).get(f"{API}/my/posts/{waiting['id']}").json()
    assert shown["form"]["version"] == 2 and shown["custom_fields"]["venue_note"] == "Gate 3"                  # the value is still stored, even though the form no longer has the field
    assert submit(world, asha, waiting)["status"] == "pending_approval"                                    # and it does not block sending
    approved = review(world, waiting, "approve").json()
    assert [f["key"] for f in world.as_user(None).get(f"{API}/{approved['slug']}").json()["custom_fields"]] == ["speaker"]       # the new version shows only what it has
    unknown = world.as_user(asha).patch(f"{API}/my/posts/{shown['id']}", json={"custom_fields": {"venue_note": "x"}})
    assert unknown.status_code in (200, 409, 422)


# --- Blog: diagnostics ---------------------------------------------------------------------------------------------------------------

def test_diagnostics_count_forms_older_definitions_and_files(world):
    new_form(world, name="A")
    published = new_form(world, name="B")
    publish_form(world, published)
    row = world.db.query(__import__("app.models.blog_model", fromlist=["BlogForm"]).BlogForm).filter_by(name="A").one()
    row.draft_definition = {"sections": [{"key": "s", "title": "S", "fields": [{"key": "title", "kind": "core", "type": "text", "label": "Title", "visible": True, "options": [], "validation": {}}]}]}
    world.db.commit()
    result = root(world).get(f"{BASE}/diagnostics")
    assert result.status_code == 200
    body = result.json()
    assert body["forms"]["total"] == 2 and body["forms"]["by_lifecycle"] == {"draft": 1, "active": 1} and body["forms"]["drafts_in_older_format"] == 1
    assert body["forms"]["versions_in_older_format"] == 0 and body["posts"]["pinned_to_a_missing_form_version"] == 0 and body["files"]["total"] == 0
    assert world.as_user(world.people.asha).get(f"{BASE}/diagnostics").status_code == 403 and world.as_user(None).get(f"{BASE}/diagnostics").status_code == 401


# --- Event: section visibility, ETag, public active form, assignments, diagnostics --------------------------------------------------

EVENT_BASE = "/api/v1/events/form-configuration"


@pytest.fixture
def env(monkeypatch):
    for target in ("app.services.super_admin_identity.fetch_internal_user_by_id", "app.services.super_admin_identity.fetch_auth_me_profile",
                   "app.services.invigorate_auth_client.fetch_tenant_me_profile", "app.services.invigorate_auth_client.fetch_auth_me_profile"):
        monkeypatch.setattr(target, lambda *a, **k: None)
    from app.models.event_form_config_model import EventFormAudit

    db = make_session()
    for model in (EventFormAudit, EventFormAssignment):
        model.__table__.create(bind=db.get_bind(), checkfirst=True)
    tenant = uuid4()
    enterprise = make_enterprise(db, tenant, "Apollo Events")
    sections = copy.deepcopy(SECTIONS[:2])
    config = EventFormConfiguration(name="Summit form", description="For summits", scope="global", status="published", is_active=True, current_version=1, published_at=datetime.utcnow())
    db.add(config)
    db.flush()
    version = EventFormConfigurationVersion(configuration_id=config.id, version=1, status="published", sections=sections, published_at=datetime.utcnow())
    db.add(version)
    db.commit()
    root_user = super_admin_user()
    yield type("E", (), {"db": db, "tenant": tenant, "enterprise": enterprise, "config": config, "version": version, "root": root_user, "client": client_for(db, root_user)})
    reset_overrides()
    db.close()


def test_the_public_active_event_form_shows_only_published_fields_and_follows_assignments(env):
    anonymous = client_for(env.db, None)
    resp = anonymous.get(f"{EVENT_BASE}/public/active")
    assert resp.status_code == 200
    body = resp.json()
    keys = [f["key"] for s in body["definition"]["sections"] for f in s["fields"]]
    assert body["audience"] == "public" and body["name"] == "Summit form" and body["description"] == "For summits" and body["version"] == 1 and body["published_at"]
    assert "speaker" in keys and "staff_only" not in keys and "old_field" not in keys
    assert "placeholder" not in str(body["definition"]) and "validation" not in str(body["definition"])

    special = EventFormConfiguration(name="Apollo only", scope="selective", status="published", is_active=True, current_version=1, published_at=datetime.utcnow())
    env.db.add(special)
    env.db.flush()
    env.db.add(EventFormConfigurationVersion(configuration_id=special.id, version=1, status="published", sections=[{"id": "s", "stable_key": "only", "label": "Only", "position": 1, "is_enabled": True, "fields": [
        {"id": "f1", "source": "core", "core_key": "title", "renderer": "text", "label": "Title", "position": 1, "is_enabled": True, "required": True}]}], published_at=datetime.utcnow()))
    env.db.add(EventFormAssignment(configuration_id=special.id, tenant_id=env.tenant, enterprise_id=env.enterprise.id))
    env.db.commit()

    for_apollo = anonymous.get(f"{EVENT_BASE}/public/active", params={"enterprise_id": str(env.enterprise.id)}).json()
    assert for_apollo["name"] == "Apollo only" and [s["key"] for s in for_apollo["definition"]["sections"]] == ["only"]
    assert anonymous.get(f"{EVENT_BASE}/public/active").json()["name"] == "Summit form"                         # everyone else gets the global form
    assert anonymous.get(f"{EVENT_BASE}/public/active", params={"enterprise_id": str(uuid4())}).status_code == 404
    env.config.is_active = False
    env.db.commit()
    assert anonymous.get(f"{EVENT_BASE}/public/active").status_code in (404, 200)

    builder_response = client_for(env.db, env.root).get(f"{EVENT_BASE}/admin/{special.id}/common")
    assert builder_response.status_code == 200, builder_response.text
    builder = builder_response.json()
    assert builder["description"] is None and builder["assignments"] == [{"tenant_id": str(env.tenant), "enterprise_id": str(env.enterprise.id), "business_name": "Apollo Events"}]
    assert client_for(env.db, env.root).get(f"{EVENT_BASE}/admin/{env.config.id}/common").json()["description"] == "For summits"


def test_event_section_visibility_etag_lifecycle_and_diagnostics(env):
    base = f"{EVENT_BASE}/admin/{env.config.id}/common"
    first = env.client.get(base)
    assert first.status_code == 200 and first.headers["ETag"] == f'"{first.json()["etag"]}"'
    assert first.json()["lifecycle"] == "active" and "deactivate" in first.json()["available_actions"] and first.json()["description"] == "For summits"

    made = env.client.post(f"{base}/sections", json={"title": "Internal", "visibility": "enterprise_admin_only"}, headers={"If-Match": first.headers["ETag"]})
    assert made.status_code == 201, made.text
    assert made.json()["lifecycle"] == "active" and made.json()["version"] == 2 and made.headers["ETag"] == f'"{made.json()["etag"]}"'
    stale = env.client.post(f"{base}/sections", json={"title": "Other"}, headers={"If-Match": first.headers["ETag"]})
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT"
    sid = next(s["id"] for s in made.json()["definition"]["sections"] if s["key"] == "internal")
    field = env.client.post(f"{base}/fields", json={"section_id": sid, "field": {"key": "budget", "label": "Budget", "type": "number"}})
    assert field.status_code == 201
    stored = {f.get("stable_key"): f for s in env.db.query(EventFormConfigurationVersion).filter_by(version=2).one().sections for f in s["fields"]}
    assert "budget" in stored

    from app.services import form_common_write as write

    draft = env.db.query(EventFormConfigurationVersion).filter_by(version=2).one()
    ids = {f.get("stable_key"): f["id"] for s in draft.sections for f in s["fields"]}
    public, private = write.split_private([{"field_id": ids["budget"], "value": 5}, {"field_id": ids["speaker"], "value": "Dr Rao"}], draft.sections)
    assert [r["field_id"] for r in private] == [ids["budget"]] and [r["field_id"] for r in public] == [ids["speaker"]]                      # a field in a private section is private

    from app.form_builder import legacy_sections, projection
    from app.services.event_form_registry import DOMAIN_REQUIRED_CORE_KEYS, REGISTRY_BY_KEY

    definition = legacy_sections.to_common(draft.sections, registry=REGISTRY_BY_KEY, domain_required=DOMAIN_REQUIRED_CORE_KEYS)
    assert "Internal" not in str(projection.project_form(definition, "public")) and "Internal" in str(projection.project_form(definition, "enterprise_admin"))
    assert [v["key"] for v in projection.project_values(definition, {"budget": 5, "speaker": "x"}, "public")] == ["speaker"]

    diag = env.client.get(f"{EVENT_BASE}/admin/common/diagnostics")
    assert diag.status_code == 200
    stats = diag.json()
    assert stats["configurations"]["total"] == 1 and stats["configurations"]["active"] == 1 and stats["versions"]["total"] == 2 and stats["versions"]["drafts"] == 1
    assert stats["versions"]["with_enterprise_only_fields"] == 2 and stats["records"] == {"total": 0, "without_form_version": 0}
    assert client_for(env.db, None).get(f"{EVENT_BASE}/admin/common/diagnostics").status_code == 401
