"""Event wishlist: save / unsave, "My wishlist", and `is_wishlisted` on the event detail and list.

Does not use silence_side_effects: that helper patches a Celery task, and none of these routes sends a notification.
"""
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.event_aux_models import EventWishlistItem
from event_sql_support import API, client_for, customer_user, make_enterprise, make_event, make_registration, make_session, reset_overrides


@pytest.fixture
def env(monkeypatch):
    for target in (
        "app.services.super_admin_identity.fetch_internal_user_by_id", "app.services.super_admin_identity.fetch_auth_me_profile",
        "app.services.invigorate_auth_client.fetch_tenant_me_profile", "app.services.invigorate_auth_client.fetch_auth_me_profile",
    ):
        monkeypatch.setattr(target, lambda *a, **k: None)
    db = make_session()
    EventWishlistItem.__table__.create(bind=db.get_bind(), checkfirst=True)
    tenant = uuid4()
    enterprise = make_enterprise(db, tenant, "Tester Shop")
    event = make_event(db, tenant, enterprise, title="Health Harmony Summit", status="published", time_zone="Asia/Kolkata",
                       start_date=datetime(2026, 10, 21, 12, 30), end_date=datetime(2026, 10, 21, 21, 30), pricing_type="paid",
                       price="100.00", currency="USD", delivery_mode="hybrid", primary_image="https://cdn.example.com/h.jpg",
                       venue={"name": "Convention Centre", "city": "Chennai"})
    yield SimpleNamespace(db=db, tenant=tenant, enterprise=enterprise, event=event, me=customer_user("me@example.com"), other=customer_user("you@example.com"))
    reset_overrides()
    db.close()


def as_user(env, user):
    return client_for(env.db, user)


def test_saving_returns_the_card_and_saving_again_is_not_an_error(env):
    client = as_user(env, env.me)

    first = client.post(f"{API}/{env.event.id}/wishlist")
    again = client.post(f"{API}/{env.event.id}/wishlist")

    assert first.status_code == 201 and again.status_code == 200
    body = first.json()
    assert again.json()["id"] == body["id"] and env.db.query(EventWishlistItem).count() == 1
    assert body["event_id"] == str(env.event.id) and body["title"] == "Health Harmony Summit"
    assert body["price"] == "100.00" and body["currency"] == "USD" and body["primary_image"] == "https://cdn.example.com/h.jpg"
    assert body["start_at"] == "2026-10-21T07:00:00Z" and body["end_at"] == "2026-10-21T16:00:00Z" and body["time_zone"] == "Asia/Kolkata"
    assert body["delivery_mode"] == "hybrid" and body["enterprise_name"] == "Tester Shop" and body["location"] == "Chennai"
    assert body["average_rating"] is None and body["reviews_count"] == 0 and body["status"] == "published"
    assert body["added_at"].endswith("Z")


def test_my_wishlist_lists_newest_first_and_only_my_own(env):
    second = make_event(env.db, env.tenant, env.enterprise, title="Second", status="published")
    client = as_user(env, env.me)
    client.post(f"{API}/{env.event.id}/wishlist")
    client.post(f"{API}/{second.id}/wishlist")

    mine = client.get(f"{API}/my/wishlist")
    theirs = as_user(env, env.other).get(f"{API}/my/wishlist")

    assert mine.status_code == 200 and [i["title"] for i in mine.json()] == ["Second", "Health Harmony Summit"]
    assert theirs.status_code == 200 and theirs.json() == []


def test_removing_works_and_removing_again_is_fine(env):
    client = as_user(env, env.me)
    client.post(f"{API}/{env.event.id}/wishlist")

    first = client.delete(f"{API}/{env.event.id}/wishlist")
    again = client.delete(f"{API}/{env.event.id}/wishlist")

    assert first.status_code == 200 and first.json() == {"ok": True, "is_wishlisted": False} and again.status_code == 200
    assert client.get(f"{API}/my/wishlist").json() == []


def test_everything_needs_a_login(env):
    anonymous = as_user(env, None)

    assert anonymous.get(f"{API}/my/wishlist").status_code == 401
    assert anonymous.post(f"{API}/{env.event.id}/wishlist").status_code == 401
    assert anonymous.delete(f"{API}/{env.event.id}/wishlist").status_code == 401


def test_an_event_the_caller_cannot_see_cannot_be_saved(env):
    draft = make_event(env.db, env.tenant, env.enterprise, title="Not yet", status="draft")

    assert as_user(env, env.me).post(f"{API}/{draft.id}/wishlist").status_code == 404
    assert as_user(env, env.me).post(f"{API}/{uuid4()}/wishlist").status_code == 404
    assert env.db.query(EventWishlistItem).count() == 0


def test_an_event_that_is_unpublished_or_deleted_drops_out_and_comes_back(env):
    client = as_user(env, env.me)
    client.post(f"{API}/{env.event.id}/wishlist")

    env.event.status = "draft"
    env.db.commit()
    assert client.get(f"{API}/my/wishlist").json() == []

    env.event.status = "published"
    env.db.commit()
    assert len(client.get(f"{API}/my/wishlist").json()) == 1

    env.event.is_deleted = True
    env.db.commit()
    assert client.get(f"{API}/my/wishlist").json() == []


def test_a_registered_person_keeps_a_cancelled_event_in_their_list(env):
    make_registration(env.db, env.event, "me@example.com")
    client = as_user(env, env.me)
    client.post(f"{API}/{env.event.id}/wishlist")
    env.event.status = "cancelled"
    env.db.commit()

    items = client.get(f"{API}/my/wishlist").json()

    assert len(items) == 1 and items[0]["status"] == "cancelled"


def test_the_event_detail_and_list_say_whether_it_is_saved(env):
    client = as_user(env, env.me)
    assert client.get(f"{API}/{env.event.id}").json()["is_wishlisted"] is False

    client.post(f"{API}/{env.event.id}/wishlist")

    assert client.get(f"{API}/{env.event.id}").json()["is_wishlisted"] is True
    listed = client.get(f"{API}/").json()["items"]
    assert [i["is_wishlisted"] for i in listed if i["id"] == str(env.event.id)] == [True]
    assert as_user(env, env.other).get(f"{API}/{env.event.id}").json()["is_wishlisted"] is False
    assert as_user(env, None).get(f"{API}/{env.event.id}").json()["is_wishlisted"] is False
    assert all(i["is_wishlisted"] is False for i in as_user(env, None).get(f"{API}/").json()["items"])
