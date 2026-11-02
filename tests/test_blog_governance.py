"""Blog governance: per-module switches, the audit trail, who may use them, and what a switched-off module does to posts."""
from sqlalchemy.exc import OperationalError

from app.services import blog_governance_service
from tests.blog_support import API, draft, live_merchant_post, make_category, world  # noqa: F401

GOV = f"{API}/admin/governance"


def root(w):
    return w.as_user(w.people.root)


def test_every_module_starts_on_and_the_super_admin_switches_them(world):
    first = root(world).get(GOV)
    assert first.status_code == 200
    assert first.json() == {"modules": {"events": True, "trainings": True, "products": True, "services": True, "general": True},
                            "updated_at": None, "updated_by": None}

    off = root(world).patch(f"{GOV}/events", json={"enabled": False})
    assert off.status_code == 200
    body = off.json()
    assert body["modules"] == {"events": False, "trainings": True, "products": True, "services": True, "general": True}
    assert body["updated_by"] == {"id": world.people.root["id"], "name": "Platform Root"} and body["updated_at"]
    assert root(world).get(GOV).json() == body
    assert world.as_user(None).get(f"{API}/governance").json()["modules"]["events"] is False        # the public read the web app uses


def test_a_change_that_changes_nothing_is_ok_and_leaves_no_audit_row(world):
    assert root(world).patch(f"{GOV}/events", json={"enabled": True}).status_code == 200
    root(world).patch(f"{GOV}/events", json={"enabled": False})
    assert root(world).patch(f"{GOV}/events", json={"enabled": False}).status_code == 200
    root(world).patch(f"{GOV}/events", json={"enabled": True})

    audit = root(world).get(f"{GOV}/audit").json()
    assert audit["pagination"]["total"] == 2
    newest, oldest = audit["items"]
    assert (oldest["module"], oldest["previous_value"], oldest["new_value"], oldest["action"]) == ("events", True, False, "disable")
    assert (newest["previous_value"], newest["new_value"], newest["action"]) == (False, True, "enable")
    assert newest["actor_id"] == world.people.root["id"] and newest["actor_name"] == "Platform Root" and newest["actor_role"] == "super_admin" and newest["created_at"]
    assert root(world).get(f"{GOV}/audit", params={"module": "services"}).json()["items"] == []
    assert root(world).get(f"{GOV}/audit", params={"module": "nope"}).json()["code"] == "INVALID_MODULE"


def test_reset_switches_everything_on_and_records_only_real_changes(world):
    assert root(world).post(f"{GOV}/reset").status_code == 200
    assert root(world).get(f"{GOV}/audit").json()["items"] == []                                      # nothing was off

    for module in ("events", "services"):
        root(world).patch(f"{GOV}/{module}", json={"enabled": False})
    done = root(world).post(f"{GOV}/reset").json()
    assert all(done["modules"].values())
    resets = [i for i in root(world).get(f"{GOV}/audit").json()["items"] if i["action"] == "reset"]
    assert sorted(i["module"] for i in resets) == ["events", "services"] and all(i["new_value"] is True for i in resets)


def test_only_the_super_admin_may_use_it_and_a_bad_module_is_422(world):
    for call in (lambda c: c.get(GOV), lambda c: c.patch(f"{GOV}/events", json={"enabled": False}), lambda c: c.post(f"{GOV}/reset"), lambda c: c.get(f"{GOV}/audit")):
        assert call(world.as_user(None)).status_code == 401
        for user in (world.people.asha, world.people.priya, world.people.doc):
            assert call(world.as_user(user)).status_code == 403
    assert world.as_user(world.people.asha).patch(f"{GOV}/events", json={"enabled": False}).json()["code"] == "FORBIDDEN"
    assert root(world).get(GOV).json()["modules"]["events"] is True                                   # the refused calls changed nothing

    bad = root(world).patch(f"{GOV}/shipping", json={"enabled": False})
    assert bad.status_code == 422 and bad.json()["code"] == "INVALID_MODULE" and bad.json()["allowed"] == ["events", "trainings", "products", "services", "general"]
    assert root(world).patch(f"{GOV}/events", json={}).status_code == 422                              # enabled is needed


def test_a_save_failure_is_a_500_and_changes_nothing(world, monkeypatch):
    def broken():
        raise OperationalError("COMMIT", {}, Exception("disk full"))

    monkeypatch.setattr(world.db, "commit", broken)
    resp = root(world).patch(f"{GOV}/events", json={"enabled": False})
    monkeypatch.undo()
    assert resp.status_code == 500 and resp.json()["code"] == "SAVE_FAILED"
    assert root(world).get(GOV).json()["modules"]["events"] is True
    assert root(world).get(f"{GOV}/audit").json()["items"] == []


def test_a_switched_off_module_refuses_new_posts_but_keeps_everything_else(world):
    cat = make_category(world)
    live = live_merchant_post(world, cat, module="events")
    keep = draft(world, world.people.asha, cat, module="events", title="A draft about events")
    assert live["module"] == "events"

    root(world).patch(f"{GOV}/events", json={"enabled": False})

    refused = world.as_user(world.people.asha).post(f"{API}/my/posts", json={"title": "Another", "module": "events"})
    assert refused.status_code == 409 and refused.json()["code"] == "MODULE_DISABLED" and refused.json()["module"] == "events"
    assert root(world).post(f"{API}/admin/posts", json={"title": "Platform", "module": "events"}).json()["code"] == "MODULE_DISABLED"
    assert world.as_user(world.people.asha).post(f"{API}/my/posts", json={"title": "Other module", "module": "general"}).status_code == 201

    assert world.as_user(None).get(f"{API}/{live['slug']}").status_code == 200                       # published: still open by its URL
    assert world.as_user(world.people.asha).get(f"{API}/my/posts/{keep['id']}").status_code == 200   # the draft is kept
    assert world.as_user(world.people.asha).patch(f"{API}/my/posts/{keep['id']}", json={"excerpt": "Still editable"}).status_code == 200
    assert world.as_user(None).get(API, params={"module": "events"}).json()["pagination"]["total"] == 1
    assert root(world).get(f"{API}/admin/categories").json()["total"] == 1

    root(world).post(f"{GOV}/reset")
    assert world.as_user(world.people.asha).post(f"{API}/my/posts", json={"title": "Back on", "module": "events"}).status_code == 201


def test_two_changes_in_a_row_both_land_and_the_last_one_wins(world):
    root(world).patch(f"{GOV}/products", json={"enabled": False})
    root(world).patch(f"{GOV}/products", json={"enabled": True})
    assert root(world).get(GOV).json()["modules"]["products"] is True
    assert root(world).get(f"{GOV}/audit").json()["pagination"]["total"] == 2
    assert blog_governance_service.MODULES == ("events", "trainings", "products", "services", "general")
