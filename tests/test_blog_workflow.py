"""Blogs and news: platform posts, merchant posts with approval, categories, who may do what, and the notifications."""
import pytest

from tests.blog_support import (
    API, BODY, TENANT_A, draft, live_merchant_post, make_category, notices_of, public_slugs, review, submit, world,  # noqa: F401
)


# --- categories -----------------------------------------------------------------------------------------------------------

def test_the_super_admin_manages_categories_and_the_public_sees_the_active_ones(world):
    root = world.people.root
    health = make_category(world, "Health")
    tips = make_category(world, "Tips")

    assert world.as_user(root).post(f"{API}/admin/categories", json={"name": " health "}).status_code == 409      # same name, any case
    assert world.as_user(root).patch(f"{API}/admin/categories/{tips['id']}", json={"is_active": False}).json()["is_active"] is False

    public = world.as_user(None).get(f"{API}/categories").json()
    assert [c["name"] for c in public["items"]] == ["Health"] and public["items"][0]["post_count"] == 0
    admin = world.as_user(root).get(f"{API}/admin/categories").json()
    assert [(c["name"], c["is_active"]) for c in admin["items"]] == [("Health", True), ("Tips", False)]

    order = world.as_user(root).put(f"{API}/admin/categories/order", json={"ids": [tips["id"], health["id"]]})
    assert [c["name"] for c in order.json()["items"]] == ["Tips", "Health"]
    assert world.as_user(root).put(f"{API}/admin/categories/order", json={"ids": [tips["id"]]}).status_code == 422


def test_a_switched_off_category_cannot_be_picked_but_old_posts_keep_it(world):
    root, asha = world.people.root, world.people.asha
    tips = make_category(world, "Tips")
    post = live_merchant_post(world, tips)

    world.as_user(root).patch(f"{API}/admin/categories/{tips['id']}", json={"is_active": False})

    assert world.as_user(None).get(f"{API}/{post['slug']}").json()["category"] == "Tips"
    resp = world.as_user(asha).post(f"{API}/my/posts", json={"title": "New", "category_id": tips["id"]})
    assert resp.status_code == 422 and "switched off" in resp.json()["detail"]


def test_renaming_a_category_renames_it_on_its_posts_and_a_used_one_cannot_be_deleted(world):
    root = world.people.root
    tips = make_category(world, "Tips")
    spare = make_category(world, "Spare")
    post = live_merchant_post(world, tips)

    renamed = world.as_user(root).patch(f"{API}/admin/categories/{tips['id']}", json={"name": "Advice"})
    assert renamed.status_code == 200 and renamed.json()["post_count"] == 1
    assert world.as_user(None).get(f"{API}/{post['slug']}").json()["category"] == "Advice"
    assert world.as_user(root).delete(f"{API}/admin/categories/{tips['id']}").status_code == 409
    assert world.as_user(root).delete(f"{API}/admin/categories/{spare['id']}").status_code == 200


def test_only_the_super_admin_manages_categories(world):
    health = make_category(world)
    for user in (world.people.asha, world.people.priya, world.people.doc):
        assert world.as_user(user).post(f"{API}/admin/categories", json={"name": "X"}).status_code == 403
        assert world.as_user(user).patch(f"{API}/admin/categories/{health['id']}", json={"name": "Y"}).status_code == 403
    assert world.as_user(None).post(f"{API}/admin/categories", json={"name": "X"}).status_code == 401


# --- platform posts ------------------------------------------------------------------------------------------------------

def admin_post(world, category, **extra):
    body = {"kind": "blog", "title": "Platform update", "excerpt": "What is new.", "content": BODY, "category_id": category["id"], **extra}
    resp = world.as_user(world.people.root).post(f"{API}/admin/posts", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_a_super_admin_post_is_live_at_once_with_no_approval(world):
    health = make_category(world)

    post = admin_post(world, health, status="published")

    assert post["status"] == "published" and post["is_live"] is True and post["author_type"] == "platform"
    assert post["author_name"] == "Platform Root" and post["published_at"]
    assert public_slugs(world) == ["platform-update"]
    assert world.as_user(None).get(f"{API}/platform-update").json()["content"] == BODY


def test_a_draft_is_not_public_until_published_and_publishing_needs_a_category_and_a_body(world):
    health = make_category(world)
    root = world.people.root
    bare = world.as_user(root).post(f"{API}/admin/posts", json={"title": "Bare"}).json()

    assert bare["status"] == "draft" and public_slugs(world) == []
    refused = world.as_user(root).post(f"{API}/admin/posts/{bare['id']}/publish")
    assert refused.status_code == 422 and refused.json()["detail"] == "Add the category, body before you publish this"
    assert world.as_user(None).get(f"{API}/{bare['slug']}").status_code == 404

    world.as_user(root).put(f"{API}/admin/posts/{bare['id']}", json={"category_id": health["id"], "content": "<p>Now it has a body</p>"})
    assert world.as_user(root).post(f"{API}/admin/posts/{bare['id']}/publish").json()["status"] == "published"
    assert public_slugs(world) == ["bare"]


def test_the_two_kinds_have_their_own_lists_and_pinned_posts_come_first(world):
    health = make_category(world)
    root = world.people.root
    first = admin_post(world, health, title="Old blog", status="published")
    news = admin_post(world, health, title="Breaking news", kind="news", status="published")
    latest = admin_post(world, health, title="Newest blog", status="published")

    assert public_slugs(world, kind="blog") == ["newest-blog", "old-blog"]
    assert public_slugs(world, kind="news") == ["breaking-news"]
    pinned = world.as_user(root).put(f"{API}/admin/posts/{first['id']}/pin", json={"pinned": True}).json()
    assert pinned["is_pinned"] is True and "unpin" in pinned["actions"]
    assert public_slugs(world, kind="blog") == ["old-blog", "newest-blog"]
    world.as_user(root).put(f"{API}/admin/posts/{first['id']}/pin", json={"pinned": False})
    assert public_slugs(world, kind="blog") == ["newest-blog", "old-blog"]
    assert latest["slug"] == "newest-blog" and news["kind"] == "news"


def test_the_public_list_filters_by_category_search_and_tag_and_pages(world):
    health = make_category(world, "Health")
    tips = make_category(world, "Tips")
    admin_post(world, health, title="Sleep well", status="published", tags=["Sleep", "night"])
    admin_post(world, tips, title="Desk stretches", status="published", tags=["office"])
    admin_post(world, tips, title="Hidden draft")

    assert public_slugs(world, category_id=tips["id"]) == ["desk-stretches"]
    assert public_slugs(world, category="health") == ["sleep-well"]
    assert public_slugs(world, search="stretch") == ["desk-stretches"]
    assert public_slugs(world, tag="sleep") == ["sleep-well"]
    assert public_slugs(world, tag="sle") == []
    page = world.as_user(None).get(API, params={"page_size": 1, "page": 2}).json()
    assert page["pagination"] == {"total": 2, "page": 2, "page_size": 1, "total_pages": 2} and len(page["items"]) == 1
    counts = {c["name"]: c["post_count"] for c in world.as_user(None).get(f"{API}/categories").json()["items"]}
    assert counts == {"Health": 1, "Tips": 1}


def test_a_view_is_counted_on_the_post_page_and_the_id_also_opens_it(world):
    post = admin_post(world, make_category(world), status="published")

    first = world.as_user(None).get(f"{API}/{post['slug']}").json()
    second = world.as_user(None).get(f"{API}/{post['id']}").json()

    assert first["view_count"] == 1 and second["view_count"] == 2
    assert "author_id" in first and first["author_id"] is None          # who wrote it is not for the public


def test_the_body_is_cleaned_of_scripts_and_unsafe_links(world):
    health = make_category(world)
    dirty = ('<p onclick="x()">Hi <script>alert(1)</script><a href="javascript:alert(1)">bad</a> '
             '<a href="https://ok.example/x" onmouseover="y()">good</a><img src="javascript:1"><iframe src="https://evil"></iframe></p>')

    post = admin_post(world, health, content=dirty, status="published")

    body = world.as_user(None).get(f"{API}/{post['slug']}").json()["content"]
    assert "script" not in body and "onclick" not in body and "javascript" not in body and "iframe" not in body and "onmouseover" not in body
    assert 'href="https://ok.example/x"' in body and "good" in body and "Hi" in body


def test_titles_summaries_and_tags_have_limits(world):
    health = make_category(world)
    root = world.people.root
    base = {"category_id": health["id"], "content": BODY}

    assert world.as_user(root).post(f"{API}/admin/posts", json={**base, "title": "x" * 151}).status_code == 422
    assert world.as_user(root).post(f"{API}/admin/posts", json={**base, "title": "ok", "excerpt": "y" * 301}).status_code == 422
    assert world.as_user(root).post(f"{API}/admin/posts", json={**base, "title": "ok", "tags": [str(i) for i in range(11)]}).status_code == 422
    assert world.as_user(root).post(f"{API}/admin/posts", json={**base, "title": "ok", "cover_image_url": "javascript:alert(1)"}).status_code == 422
    ok = world.as_user(root).post(f"{API}/admin/posts", json={**base, "title": "ok", "tags": ["a", "A", " b "], "cover_image_url": "/api/v1/uploads/blogs/c.jpg"})
    assert ok.status_code == 201 and ok.json()["tags"] == ["a", "b"]


# --- a merchant's post: draft, send, decision ----------------------------------------------------------------------------

def test_a_merchant_saves_a_draft_for_their_own_business_and_nobody_else_can_see_it(world):
    health = make_category(world)
    post = draft(world, world.people.asha, health)

    assert post["status"] == "draft" and post["status_label"] == "Draft" and post["author_type"] == "merchant"
    assert post["business_name"] == "Calm Clinic" and post["business_logo_url"] == "https://cdn.example.test/calm.png"
    assert post["author_name"] == "Asha Merchant" and post["tenant_id"] == str(TENANT_A)
    assert post["actions"] == ["edit", "submit", "delete"]
    assert public_slugs(world) == []
    assert world.as_user(None).get(f"{API}/{post['slug']}").status_code == 404
    # another business sees nothing of it, not even that it exists
    assert world.as_user(world.people.bela).get(f"{API}/my/posts/{post['id']}").status_code == 404
    assert world.as_user(world.people.bela).get(f"{API}/my/posts").json()["items"] == []
    assert world.as_user(world.people.bela).patch(f"{API}/my/posts/{post['id']}", json={"title": "Mine now"}).status_code == 404
    # a colleague at the same business does
    assert world.as_user(world.people.asha2).get(f"{API}/my/posts/{post['id']}").status_code == 200


def test_who_may_use_the_merchant_routes(world):
    health = make_category(world)
    for user in (world.people.priya, world.people.doc, world.people.root):
        assert world.as_user(user).post(f"{API}/my/posts", json={"title": "x", "category_id": health["id"]}).status_code == 403
    assert world.as_user(None).get(f"{API}/my/posts").status_code == 401


def test_submitting_needs_the_whole_post_and_tells_the_super_admins(world):
    health = make_category(world)
    asha = world.people.asha
    bare = world.as_user(asha).post(f"{API}/my/posts", json={"title": "Just a title"}).json()

    refused = world.as_user(asha).post(f"{API}/my/posts/{bare['id']}/submit")
    assert refused.status_code == 422 and refused.json()["detail"] == "Add the category, body before you send this for approval"

    post = draft(world, asha, health)
    sent = submit(world, asha, post)
    assert sent["status"] == "pending_approval" and sent["submitted_at"] and sent["actions"] == []
    [notice] = notices_of(world, "blog_post_submitted")
    assert {str(u) for u in notice["user_ids"]} == {world.people.root["id"], world.people.root2["id"]}
    meta = notice["metadata"]
    assert meta["post_id"] == post["id"] and meta["category"] == "blog_post_submitted"
    assert notice["title"] == "New post waiting for approval" and 'Calm Clinic sent the blog post "Five calm habits"' in notice["message"]
    assert meta["entity_type"] == "blog_post" and meta["is_edit"] is False and meta["business_name"] == "Calm Clinic"


def test_a_post_waiting_for_review_cannot_be_edited_or_sent_again(world):
    post = draft(world, world.people.asha, make_category(world))
    submit(world, world.people.asha, post)

    assert world.as_user(world.people.asha).patch(f"{API}/my/posts/{post['id']}", json={"title": "Sneaky"}).status_code == 409
    assert world.as_user(world.people.asha).post(f"{API}/my/posts/{post['id']}/submit").status_code == 409
    assert world.as_user(world.people.asha).delete(f"{API}/my/posts/{post['id']}").status_code == 409


def test_the_queue_lists_pending_posts_oldest_first_with_filters(world):
    news_cat, blog_cat = make_category(world, "News desk"), make_category(world, "Health")
    a = draft(world, world.people.asha, blog_cat, title="First sent")
    b = draft(world, world.people.bela, news_cat, title="Second sent", kind="news")
    c = draft(world, world.people.asha, blog_cat, title="Never sent")
    submit(world, world.people.bela, b)
    submit(world, world.people.asha, a)
    root = world.people.root

    queue = world.as_user(root).get(f"{API}/admin/queue").json()
    assert [i["title"] for i in queue["items"]] == ["Second sent", "First sent"] and queue["pagination"]["total"] == 2
    assert [i["title"] for i in world.as_user(root).get(f"{API}/admin/queue", params={"kind": "news"}).json()["items"]] == ["Second sent"]
    assert [i["title"] for i in world.as_user(root).get(f"{API}/admin/queue", params={"category_id": blog_cat["id"]}).json()["items"]] == ["First sent"]
    by_business = world.as_user(root).get(f"{API}/admin/queue", params={"enterprise_id": a["enterprise_id"]}).json()["items"]
    assert [i["title"] for i in by_business] == ["First sent"]
    assert queue["items"][0]["actions"] == ["edit", "review", "delete", "pin"] and c["status"] == "draft"


def test_approving_publishes_it_and_tells_the_author(world):
    post = draft(world, world.people.asha, make_category(world))
    submit(world, world.people.asha, post)

    done = review(world, post, "approve")

    assert done.status_code == 200 and done.json()["status"] == "published" and done.json()["published_at"]
    assert public_slugs(world) == ["five-calm-habits"]
    public = world.as_user(None).get(f"{API}/five-calm-habits").json()
    assert public["business_name"] == "Calm Clinic" and public["author_type"] == "merchant" and public["status"] == "published"
    [notice] = notices_of(world, "blog_post_approved")
    assert [str(u) for u in notice["user_ids"]] == [world.people.asha["id"]]
    assert notice["message"] == 'Your post "Five calm habits" was approved and is now live.' and "reason" not in notice["metadata"]


@pytest.mark.parametrize("action,status_after,category", [
    ("request_changes", "changes_requested", "blog_post_changes_requested"), ("reject", "rejected", "blog_post_rejected"),
])
def test_asking_for_changes_or_rejecting_needs_a_reason_the_merchant_can_read(world, action, status_after, category):
    post = draft(world, world.people.asha, make_category(world))
    submit(world, world.people.asha, post)

    assert review(world, post, action).status_code == 422
    assert review(world, post, action, "no").status_code == 422
    assert world.notices and not notices_of(world, category)
    done = review(world, post, action, "Please add a source for the claim in paragraph two.")

    assert done.status_code == 200 and done.json()["status"] == status_after
    mine = world.as_user(world.people.asha).get(f"{API}/my/posts/{post['id']}").json()
    assert mine["reviewer_note"] == "Please add a source for the claim in paragraph two." and mine["status"] == status_after
    assert "edit" in mine["actions"] and "submit" in mine["actions"]
    assert public_slugs(world) == []
    [notice] = notices_of(world, category)
    assert notice["metadata"]["reason"] == "Please add a source for the claim in paragraph two."
    assert "source" not in notice["message"]


def test_the_merchant_fixes_a_sent_back_post_and_sends_it_again(world):
    post = draft(world, world.people.asha, make_category(world))
    submit(world, world.people.asha, post)
    review(world, post, "request_changes", "Shorten the intro please")

    edited = world.as_user(world.people.asha).patch(f"{API}/my/posts/{post['id']}", json={"content": "<p>Shorter.</p>"}).json()
    assert edited["status"] == "changes_requested" and edited["content"] == "<p>Shorter.</p>"
    again = submit(world, world.people.asha, post)
    assert again["status"] == "pending_approval" and again["reviewer_note"] is None
    assert len(notices_of(world, "blog_post_submitted")) == 2           # each submission is announced
    assert review(world, post, "approve").json()["status"] == "published"


def test_a_merchant_cannot_approve_or_use_the_admin_routes_and_cannot_review_their_own_post(world):
    post = draft(world, world.people.asha, make_category(world))
    submit(world, world.people.asha, post)

    for user in (world.people.asha, world.people.bela, world.people.priya):
        assert review(world, post, "approve", user=user).status_code == 403
        assert world.as_user(user).get(f"{API}/admin/queue").status_code == 403
        assert world.as_user(user).get(f"{API}/admin/posts").status_code == 403
        assert world.as_user(user).post(f"{API}/admin/posts", json={"title": "x"}).status_code == 403
    assert world.as_user(None).post(f"{API}/admin/posts/{post['id']}/review", json={"action": "approve"}).status_code == 401
    assert world.as_user(world.people.asha).get(f"{API}/my/posts/{post['id']}").json()["status"] == "pending_approval"


def test_only_a_pending_post_can_be_reviewed(world):
    post = draft(world, world.people.asha, make_category(world))

    assert review(world, post, "approve").status_code == 409          # still a draft


def test_a_super_admin_can_publish_a_merchants_draft_directly_and_the_author_is_told(world):
    post = draft(world, world.people.asha, make_category(world))

    done = world.as_user(world.people.root).post(f"{API}/admin/posts/{post['id']}/publish")

    assert done.status_code == 200 and done.json()["status"] == "published"
    assert len(notices_of(world, "blog_post_approved")) == 1


# --- editing a live post ---------------------------------------------------------------------------------------------------

def test_editing_a_live_post_keeps_the_old_version_up_until_the_edit_is_approved(world):
    health = make_category(world)
    post = live_merchant_post(world, health, title="Original title")
    asha = world.people.asha

    edited = world.as_user(asha).patch(f"{API}/my/posts/{post['id']}", json={"title": "Better title", "content": "<p>New body</p>"}).json()

    assert edited["status"] == "pending_approval" and edited["is_live"] is True and edited["has_pending_revision"] is True
    assert edited["title"] == "Better title" and edited["live_version"]["title"] == "Original title"
    public = world.as_user(None).get(f"{API}/original-title").json()
    assert public["title"] == "Original title" and public["content"] == BODY          # the public still reads the approved version
    assert public_slugs(world) == ["original-title"]
    [_, edit_notice] = notices_of(world, "blog_post_submitted")
    assert edit_notice["metadata"]["is_edit"] is True and "edited" in edit_notice["message"]
    # the Super Admin cannot overwrite it while it waits, and the queue shows it
    assert world.as_user(world.people.root).put(f"{API}/admin/posts/{post['id']}", json={"title": "X"}).status_code == 409
    assert [i["title"] for i in world.as_user(world.people.root).get(f"{API}/admin/queue").json()["items"]] == ["Better title"]

    assert review(world, post, "approve").json()["status"] == "published"
    now = world.as_user(None).get(f"{API}/original-title").json()
    assert now["title"] == "Better title" and now["content"] == "<p>New body</p>"
    assert world.as_user(world.people.asha).get(f"{API}/my/posts/{post['id']}").json()["has_pending_revision"] is False


def test_a_rejected_edit_leaves_the_old_version_live_and_can_be_discarded(world):
    post = live_merchant_post(world, make_category(world), title="Original title")
    asha = world.people.asha
    world.as_user(asha).patch(f"{API}/my/posts/{post['id']}", json={"title": "Risky title"})

    review(world, post, "reject", "This claim is not supported")

    mine = world.as_user(asha).get(f"{API}/my/posts/{post['id']}").json()
    assert mine["status"] == "rejected" and mine["is_live"] is True and mine["title"] == "Risky title"
    assert "discard_changes" in mine["actions"] and world.as_user(None).get(f"{API}/original-title").json()["title"] == "Original title"
    back = world.as_user(asha).post(f"{API}/my/posts/{post['id']}/discard-changes").json()
    assert back["status"] == "published" and back["title"] == "Original title" and back["has_pending_revision"] is False


def test_a_super_admin_edit_of_a_live_post_applies_at_once(world):
    health = make_category(world)
    post = admin_post(world, health, status="published")

    world.as_user(world.people.root).put(f"{API}/admin/posts/{post['id']}", json={"title": "Edited by admin", "excerpt": "<b>Bold</b> summary"})

    public = world.as_user(None).get(f"{API}/{post['slug']}").json()
    assert public["title"] == "Edited by admin" and public["excerpt"] == "Bold summary" and public["slug"] == post["slug"]


# --- taking down and deleting -------------------------------------------------------------------------------------------------

def test_a_merchant_takes_down_their_live_post_and_can_send_it_again(world):
    post = live_merchant_post(world, make_category(world))
    asha = world.people.asha

    down = world.as_user(asha).post(f"{API}/my/posts/{post['id']}/unpublish").json()

    assert down["status"] == "unpublished" and down["is_live"] is False and public_slugs(world) == []
    assert world.as_user(asha).delete(f"{API}/my/posts/{post['id']}").status_code == 409          # only drafts can be deleted
    assert submit(world, asha, post)["status"] == "pending_approval"
    review(world, post, "approve")
    assert public_slugs(world) == ["five-calm-habits"]


def test_taking_down_a_post_that_is_not_live_is_refused(world):
    post = draft(world, world.people.asha, make_category(world))

    assert world.as_user(world.people.asha).post(f"{API}/my/posts/{post['id']}/unpublish").status_code == 409


def test_a_super_admin_needs_a_reason_to_take_down_a_merchants_post_and_the_merchant_is_told(world):
    post = live_merchant_post(world, make_category(world))
    root = world.people.root

    assert world.as_user(root).post(f"{API}/admin/posts/{post['id']}/unpublish").status_code == 422
    done = world.as_user(root).post(f"{API}/admin/posts/{post['id']}/unpublish", json={"reason": "Out of date medical advice"})

    assert done.json()["status"] == "unpublished" and done.json()["reviewer_note"] == "Out of date medical advice"
    assert public_slugs(world) == []
    [notice] = notices_of(world, "blog_post_unpublished")
    assert notice["metadata"]["reason"] == "Out of date medical advice"
    assert world.as_user(world.people.asha).get(f"{API}/my/posts/{post['id']}").json()["reviewer_note"] == "Out of date medical advice"


def test_a_platform_post_can_be_taken_down_without_a_reason(world):
    post = admin_post(world, make_category(world), status="published")

    assert world.as_user(world.people.root).post(f"{API}/admin/posts/{post['id']}/unpublish").json()["status"] == "unpublished"
    assert not notices_of(world, "blog_post_unpublished")


def test_a_deleted_post_is_hidden_everywhere_but_kept_for_the_record(world):
    post = live_merchant_post(world, make_category(world))
    root = world.people.root

    assert world.as_user(root).delete(f"{API}/admin/posts/{post['id']}").status_code == 422             # a merchant's post needs a reason
    assert world.as_user(root).delete(f"{API}/admin/posts/{post['id']}", params={"reason": "Spam content"}).status_code == 200

    assert public_slugs(world) == [] and world.as_user(None).get(f"{API}/{post['slug']}").status_code == 404
    assert world.as_user(world.people.asha).get(f"{API}/my/posts/{post['id']}").status_code == 404
    assert world.as_user(world.people.asha).get(f"{API}/my/posts").json()["items"] == []
    assert world.as_user(root).get(f"{API}/admin/posts").json()["items"] == []
    kept = world.as_user(root).get(f"{API}/admin/posts", params={"status": "deleted"}).json()["items"]
    assert [i["id"] for i in kept] == [post["id"]] and kept[0]["actions"] == []
    [notice] = notices_of(world, "blog_post_deleted")
    assert notice["metadata"]["reason"] == "Spam content"
    assert world.as_user(root).get(f"{API}/admin/posts/{post['id']}").status_code == 404
    assert world.as_user(root).put(f"{API}/admin/posts/{post['id']}/pin", json={"pinned": True}).status_code == 404


def test_a_merchant_deletes_their_own_draft(world):
    post = draft(world, world.people.asha, make_category(world))

    assert world.as_user(world.people.asha).delete(f"{API}/my/posts/{post['id']}").status_code == 200
    assert world.as_user(world.people.asha).get(f"{API}/my/posts").json()["items"] == []
    assert world.as_user(world.people.bela).delete(f"{API}/my/posts/{post['id']}").status_code == 404


def test_the_category_with_only_deleted_posts_still_cannot_be_deleted(world):
    health = make_category(world)
    post = draft(world, world.people.asha, health)
    world.as_user(world.people.asha).delete(f"{API}/my/posts/{post['id']}")

    assert world.as_user(world.people.root).delete(f"{API}/admin/categories/{health['id']}").status_code == 409


# --- history, lists and the older status endpoint ----------------------------------------------------------------------------

def test_the_history_says_who_did_what_and_why(world):
    post = draft(world, world.people.asha, make_category(world))
    submit(world, world.people.asha, post)
    review(world, post, "request_changes", "Add a source please")
    world.as_user(world.people.asha).patch(f"{API}/my/posts/{post['id']}", json={"title": "Sourced"})

    root_view = world.as_user(world.people.root).get(f"{API}/admin/posts/{post['id']}/history").json()
    own_view = world.as_user(world.people.asha).get(f"{API}/my/posts/{post['id']}/history").json()

    assert [e["action"] for e in root_view["items"]] == ["edited", "changes_requested", "submitted", "created"]
    assert root_view == own_view and root_view["total"] == 4
    decision = root_view["items"][1]
    assert decision["actor_name"] == "Platform Root" and decision["actor_role"] == "super_admin" and decision["reason"] == "Add a source please"
    assert decision["from_status"] == "pending_approval" and decision["to_status"] == "changes_requested"
    assert world.as_user(world.people.bela).get(f"{API}/my/posts/{post['id']}/history").status_code == 404
    assert world.as_user(world.people.asha).get(f"{API}/admin/posts/{post['id']}/history").status_code == 403


def test_my_posts_filters_by_status_and_kind(world):
    health = make_category(world)
    asha = world.people.asha
    sent = draft(world, asha, health, title="Sent one")
    draft(world, asha, health, title="Still a draft", kind="news")
    submit(world, asha, sent)

    assert [p["title"] for p in world.as_user(asha).get(f"{API}/my/posts", params={"status": "pending_approval"}).json()["items"]] == ["Sent one"]
    assert [p["title"] for p in world.as_user(asha).get(f"{API}/my/posts", params={"kind": "news"}).json()["items"]] == ["Still a draft"]
    assert world.as_user(asha).get(f"{API}/my/posts", params={"status": "nonsense"}).status_code == 422


def test_the_admin_list_filters_by_source_status_and_search(world):
    health = make_category(world)
    admin_post(world, health, title="From the platform", status="published")
    mine = draft(world, world.people.asha, health, title="From a merchant")
    root = world.people.root

    assert [p["title"] for p in world.as_user(root).get(f"{API}/admin/posts", params={"source": "merchant"}).json()["items"]] == ["From a merchant"]
    assert [p["title"] for p in world.as_user(root).get(f"{API}/admin/posts", params={"status": "published"}).json()["items"]] == ["From the platform"]
    assert [p["id"] for p in world.as_user(root).get(f"{API}/admin/posts", params={"search": "Calm Clinic"}).json()["items"]] == [mine["id"]]


def test_the_older_status_endpoint_still_publishes_and_archives(world):
    post = admin_post(world, make_category(world))
    root = world.people.root

    assert world.as_user(root).patch(f"{API}/admin/posts/{post['id']}/status", json={"status": "published"}).json()["status"] == "published"
    assert world.as_user(root).patch(f"{API}/admin/posts/{post['id']}/status", json={"status": "archived"}).json()["status"] == "unpublished"
    assert world.as_user(root).patch(f"{API}/admin/posts/{post['id']}/status", json={"status": "draft"}).json()["status"] == "draft"
    assert public_slugs(world) == []


def test_a_failing_notification_never_breaks_the_call(world, monkeypatch):
    post = draft(world, world.people.asha, make_category(world))
    monkeypatch.setattr("app.services.notification_service.create_automatic_notification",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("queue down")))

    assert world.as_user(world.people.asha).post(f"{API}/my/posts/{post['id']}/submit").status_code == 200
    assert review(world, post, "approve").status_code == 200
    assert BODY and public_slugs(world) == ["five-calm-habits"]


def test_the_push_payload_leaves_the_reason_out():
    from app.services import notification_delivery_service

    assert "reason" in notification_delivery_service.PUSH_OMITTED_METADATA_KEYS
