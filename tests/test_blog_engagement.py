"""Blog engagement: likes, saved blogs, comments with replies and edits, reports, and the Enterprise Admin / Super Admin moderation."""
from time import sleep

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import blog_engagement
from app.core.dependencies import get_current_user, get_optional_current_user
from app.db.database import Base
from app.models.blog_engagement_model import BlogComment, BlogCommentEvent, BlogCommentReport, BlogLike, BlogWishlist
from app.services import blog_engagement_service as svc
from app.services.event_discussion_moderation import ModerationUnavailable, Verdict
from tests.blog_support import API, draft, live_merchant_post, make_category, notices_of, person, world  # noqa: F401

ENG = f"{API}/admin/engagement"


@pytest.fixture
def eng(world, monkeypatch):
    """The blog world with the engagement routes, a signed-in caller you choose, and an AI check driven by the words in the text."""
    Base.metadata.create_all(world.db.get_bind(), tables=[m.__table__ for m in (BlogLike, BlogWishlist, BlogComment, BlogCommentReport, BlogCommentEvent)])
    world.app.include_router(blog_engagement.router, prefix=API)
    state = {"user": None}

    def current():
        if state["user"] is None:
            raise HTTPException(401, "Not authenticated")
        return state["user"]

    world.app.dependency_overrides[get_current_user] = current
    world.app.dependency_overrides[get_optional_current_user] = lambda: state["user"]

    def act(user):
        state["user"] = user
        return world.as_user(user)

    def judge(text):
        if "XXKILL" in text:
            return Verdict("extreme", "Threatens someone", ["harassment"], "test")
        if "XXBORDER" in text:
            return Verdict("flagged", "Could be unkind", ["offensive"], "test")
        if "XXDOWN" in text:
            raise ModerationUnavailable()
        return Verdict("safe", None, [], "test")

    monkeypatch.setattr(svc, "classify", judge)
    category = make_category(world)
    post = live_merchant_post(world, category)
    world.act, world.post, world.category = act, post, category
    world.crowd = [person(f"Reader{n} Person", "customer") for n in range(6)]
    return world


def comment(w, user, text, post=None, parent=None):
    body = {"body": text, **({"parent_id": parent} if parent else {})}
    return w.act(user).post(f"{API}/{(post or w.post)['id']}/comments", json=body)


def public_comments(w, post=None, user=None):
    resp = w.act(user).get(f"{API}/{(post or w.post)['id']}/comments")
    assert resp.status_code == 200, resp.text
    return resp.json()


def queue(w, name):
    resp = w.act(w.people.root).get(f"{ENG}/ai-moderation", params={"queue": name})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------- likes and saves

def test_like_and_unlike_are_idempotent_and_show_on_the_blog(eng):
    w, priya, pid = eng, eng.people.priya, eng.post["id"]
    first = w.act(priya).post(f"{API}/{pid}/like")
    assert first.status_code == 200 and first.json() == {"liked": True, "wishlisted": False, "likes_count": 1, "wishlist_count": 0}
    assert w.act(priya).post(f"{API}/{pid}/like").json()["likes_count"] == 1                     # again: nothing changes
    assert w.act(w.crowd[0]).post(f"{API}/{pid}/like").json()["likes_count"] == 2

    detail = w.act(priya).get(f"{API}/{eng.post['slug']}").json()
    assert detail["engagement"] == {"likes_count": 2, "comments_count": 0, "wishlist_count": 0, "is_liked_by_me": True, "is_wishlisted_by_me": False}
    anonymous = w.act(None).get(f"{API}/{eng.post['slug']}").json()["engagement"]
    assert anonymous["likes_count"] == 2 and anonymous["is_liked_by_me"] is None and anonymous["is_wishlisted_by_me"] is None
    listed = w.act(priya).get(API).json()["items"][0]
    assert listed["engagement"]["is_liked_by_me"] is True

    assert w.act(priya).delete(f"{API}/{pid}/like").json()["liked"] is False
    assert w.act(priya).delete(f"{API}/{pid}/like").status_code == 200                          # again: still fine
    assert w.db.query(BlogLike).count() == 1
    assert w.act(None).post(f"{API}/{pid}/like").status_code == 401


def test_news_has_no_engagement_and_cannot_be_liked(eng):
    w = eng
    news = live_merchant_post(w, w.category, kind="news", title="A news item")
    assert w.act(w.people.priya).post(f"{API}/{news['id']}/like").status_code == 409
    assert w.act(w.people.priya).post(f"{API}/{news['id']}/comments", json={"body": "hi"}).status_code == 409
    assert w.act(None).get(f"{API}/{news['slug']}").json()["engagement"] is None
    assert all(i["engagement"] is None for i in w.act(None).get(API, params={"kind": "news"}).json()["items"])


def test_saved_blogs_are_listed_newest_save_first_and_paged(eng):
    w, priya = eng, eng.people.priya
    posts = [w.post] + [live_merchant_post(w, w.category, title=f"Another habit {n}") for n in range(2)]
    for p in posts:
        assert w.act(priya).post(f"{API}/{p['id']}/wishlist").json()["wishlisted"] is True
        sleep(0.01)
    assert w.act(priya).post(f"{API}/{posts[0]['id']}/wishlist").json()["wishlist_count"] == 1    # saving twice keeps the first time
    saved = w.act(priya).get(f"{API}/wishlist/my").json()
    assert [i["id"] for i in saved["items"]] == [posts[2]["id"], posts[1]["id"], posts[0]["id"]]
    assert all(i["saved_at"] for i in saved["items"]) and saved["items"][0]["engagement"]["is_wishlisted_by_me"] is True
    page = w.act(priya).get(f"{API}/wishlist/my", params={"page": 2, "page_size": 2}).json()
    assert [i["id"] for i in page["items"]] == [posts[0]["id"]] and page["pagination"]["total"] == 3

    w.act(w.people.root).post(f"{API}/admin/posts/{posts[2]['id']}/unpublish", json={"reason": "Outdated"})
    assert [i["id"] for i in w.act(priya).get(f"{API}/wishlist/my").json()["items"]] == [posts[1]["id"], posts[0]["id"]]
    assert w.act(priya).delete(f"{API}/{posts[2]['id']}/wishlist").status_code == 200           # removing a save works after take-down
    assert w.act(None).get(f"{API}/wishlist/my").status_code == 401
    assert w.db.query(BlogWishlist).filter_by(user_id=__import__("uuid").UUID(priya["id"])).count() == 2


# ---------------------------------------------------------------- posting

def test_a_safe_comment_is_published_with_a_short_name(eng):
    w = eng
    resp = comment(w, w.people.priya, "  Thank   you, this helped a lot.  ")
    assert resp.status_code == 201
    body = resp.json()
    assert body["outcome"] == "published" and body["comment"]["body"] == "Thank you, this helped a lot."
    assert body["comment"]["author"] == {"name": "Priya C.", "is_me": True} and body["comment"]["is_edited"] is False

    listing = public_comments(w)                          # anonymous
    assert listing["comments_count"] == 1
    only = listing["items"][0]
    assert only["author"] == {"name": "Priya C.", "is_me": False} and only["can_edit"] is False and only["replies"] == []
    assert "Customer" not in str(listing) and w.people.priya["id"] not in str(listing) and "@" not in str(listing)
    assert w.act(w.people.priya).get(f"{API}/{w.post['slug']}").json()["engagement"]["comments_count"] == 1
    assert public_comments(w, user=w.people.priya)["items"][0]["can_edit"] is True


def test_a_borderline_comment_waits_and_only_its_author_sees_it(eng):
    w = eng
    resp = comment(w, w.people.priya, "XXBORDER maybe unkind")
    assert resp.status_code == 202 and resp.json()["outcome"] == "held_for_review"
    assert resp.json()["comment"]["status"] == "pending_review"
    assert public_comments(w)["items"] == [] and public_comments(w)["comments_count"] == 0
    assert [c["status"] for c in public_comments(w, user=w.people.priya)["items"]] == ["pending_review"]
    assert public_comments(w, user=w.crowd[0])["items"] == []
    held = queue(w, "awaiting_review")
    assert held["counts"]["awaiting_review"] == 1 and held["items"][0]["body"] == "XXBORDER maybe unkind" and held["items"][0]["moderation_reason"] == "Could be unkind"


def test_an_extreme_comment_is_refused_with_422_but_kept_for_the_admins(eng):
    w = eng
    resp = comment(w, w.people.priya, "XXKILL you")
    assert resp.status_code == 422
    assert resp.json()["outcome"] == "removed_by_ai" and resp.json()["comment"] is None and "guidelines" in resp.json()["message"]
    assert public_comments(w, user=w.people.priya)["items"] == []
    removed = queue(w, "auto_removed")["items"]
    assert [r["body"] for r in removed] == ["XXKILL you"] and removed[0]["removed_by_role"] == "system" and removed[0]["author_id"] == w.people.priya["id"]
    history = w.act(w.people.root).get(f"{ENG}/moderation-history").json()["items"]
    assert history[0]["action"] == "created" and history[0]["to_status"] == "removed_by_ai" and history[0]["body_snapshot"] == "XXKILL you"
    for text in (str(public_comments(w)), str(w.act(None).get(f"{API}/{w.post['slug']}").json())):
        assert "XXKILL" not in text


def test_when_the_ai_is_down_the_comment_waits_and_is_checked_later(eng, monkeypatch):
    w = eng
    resp = comment(w, w.people.priya, "XXDOWN hello")
    assert resp.status_code == 202 and resp.json()["outcome"] == "pending_moderation"
    assert public_comments(w)["items"] == [] and queue(w, "awaiting_review")["counts"]["awaiting_review"] == 1
    assert svc.recheck_pending(w.db) == 0                                         # still down
    monkeypatch.setattr(svc, "classify", lambda t: Verdict("safe", None, [], "test"))
    assert svc.recheck_pending(w.db) == 1
    assert [c["body"] for c in public_comments(w)["items"]] == ["XXDOWN hello"]


def test_length_empty_and_rate_limit(eng, monkeypatch):
    w, priya = eng, eng.people.priya
    assert comment(w, priya, "x" * 501).json()["code"] == "COMMENT_TOO_LONG"
    assert comment(w, priya, "x" * 500).status_code == 201
    assert comment(w, priya, "   ").status_code in (422,)
    assert comment(w, None, "hello").status_code == 401
    monkeypatch.setattr(svc.settings, "BLOG_COMMENT_RATE_LIMIT", 3)
    codes = [comment(w, priya, f"comment {n}").status_code for n in range(4)]
    assert codes == [201, 201, 429, 429]
    assert comment(w, priya, "again").json()["code"] == "RATE_LIMITED"
    assert comment(w, w.crowd[0], "someone else is fine").status_code == 201


# ---------------------------------------------------------------- replies, edits, deletes

def test_replies_are_nested_one_level_deep(eng):
    w = eng
    top = comment(w, w.people.priya, "Top comment").json()["comment"]
    reply = comment(w, w.crowd[0], "A reply", parent=top["id"])
    assert reply.status_code == 201 and reply.json()["comment"]["parent_id"] == top["id"]
    deeper = comment(w, w.crowd[1], "Reply to the reply", parent=reply.json()["comment"]["id"])
    assert deeper.json()["comment"]["parent_id"] == top["id"]                       # attached to the top-level comment
    listing = public_comments(w)
    assert listing["comments_count"] == 3 and len(listing["items"]) == 1
    node = listing["items"][0]
    assert node["reply_count"] == 2 and [r["body"] for r in node["replies"]] == ["A reply", "Reply to the reply"]
    assert comment(w, w.crowd[2], "x", parent="00000000-0000-0000-0000-000000000000").status_code == 404
    held = comment(w, w.crowd[3], "XXBORDER held").json()["comment"]
    assert comment(w, w.crowd[4], "reply to a held one", parent=held["id"]).status_code == 404


def test_an_author_edits_and_the_edit_is_checked_again(eng):
    w, priya = eng, eng.people.priya
    mine = comment(w, priya, "First version").json()["comment"]
    other = w.act(w.crowd[0]).patch(f"{API}/comments/{mine['id']}", json={"body": "hijack"})
    assert other.status_code == 404
    ok = w.act(priya).patch(f"{API}/comments/{mine['id']}", json={"body": "Second version"})
    assert ok.status_code == 200
    assert ok.json()["comment"]["body"] == "Second version" and ok.json()["comment"]["is_edited"] is True and ok.json()["comment"]["edited_at"]

    refused = w.act(priya).patch(f"{API}/comments/{mine['id']}", json={"body": "XXKILL now"})
    assert refused.status_code == 422 and refused.json()["outcome"] == "removed_by_ai"
    assert public_comments(w)["items"][0]["body"] == "Second version"               # unchanged
    assert [e["body_snapshot"] for e in w.act(w.people.root).get(f"{ENG}/moderation-history", params={"action": "edit_rejected"}).json()["items"]] == ["XXKILL now"]

    held = w.act(priya).patch(f"{API}/comments/{mine['id']}", json={"body": "XXBORDER third"})
    assert held.status_code == 202 and held.json()["outcome"] == "held_for_review"
    assert public_comments(w)["items"] == []                                         # hidden again until a moderator decides
    assert w.act(priya).patch(f"{API}/comments/{mine['id']}", json={"body": "y" * 501}).status_code == 422


def test_an_author_deletes_and_replies_leave_a_placeholder(eng):
    w, priya = eng, eng.people.priya
    top = comment(w, priya, "Parent").json()["comment"]
    reply = comment(w, w.crowd[0], "Child", parent=top["id"]).json()["comment"]
    assert w.act(w.crowd[1]).delete(f"{API}/comments/{top['id']}").status_code == 404
    assert w.act(priya).delete(f"{API}/comments/{top['id']}").json() == {"deleted": True}
    assert w.act(priya).delete(f"{API}/comments/{top['id']}").status_code == 200       # again: fine
    node = public_comments(w)["items"][0]
    assert node["status"] == "unavailable" and node["body"] is None and node["author"]["name"] is None and node["replies"][0]["body"] == "Child"
    assert w.act(w.crowd[0]).delete(f"{API}/comments/{reply['id']}").status_code == 200
    assert public_comments(w)["items"] == []
    assert w.act(priya).patch(f"{API}/comments/{top['id']}", json={"body": "back"}).status_code == 404


# ---------------------------------------------------------------- reports

def report(w, user, cid, reason="offensive"):
    return w.act(user).post(f"{API}/comments/{cid}/report", json={"reason": reason})


def test_one_severe_report_makes_the_ai_judge_again_and_removes_an_extreme_comment(eng, monkeypatch):
    w = eng
    cid = comment(w, w.people.priya, "Looks fine at first").json()["comment"]["id"]
    monkeypatch.setattr(svc, "classify", lambda t: Verdict("extreme", "Threatens someone", ["harassment"], "test"))        # the second look
    assert report(w, w.crowd[0], cid, "harassment").json() == {"reported": True}
    assert report(w, w.crowd[1], cid, "spam").status_code == 404                        # it is gone: nothing left to report
    assert public_comments(w)["items"] == []
    row = queue(w, "auto_removed")["items"][0]
    assert row["id"] == cid and row["report_reasons"] == {"harassment": 1}
    told = notices_of(w, "blog_comment_removed")
    assert len(told) == 1 and told[0]["user_ids"] == [__import__("uuid").UUID(w.people.priya["id"])] and "reason" in told[0]["metadata"]
    assert w.db.query(BlogCommentEvent).filter_by(action="ai_removed_after_report").count() == 1


def test_a_severe_report_on_a_comment_the_ai_still_likes_waits_in_the_reported_queue(eng):
    w = eng
    cid = comment(w, w.people.priya, "Questionable but allowed").json()["comment"]["id"]
    assert report(w, w.crowd[0], cid, "unsafe_medical").status_code == 200
    assert public_comments(w)["comments_count"] == 1                                    # still up
    reported = queue(w, "user_reports")
    assert reported["counts"]["user_reports"] == 1 and reported["items"][0]["report_reasons"] == {"unsafe_medical": 1}
    assert notices_of(w, "blog_comment_removed") == []
    again = w.act(w.people.root).post(f"{ENG}/comments/{cid}/remove", json={"reason": "Medical claim without a source"})
    assert again.status_code == 200 and again.json()["status"] == "removed_by_admin"
    assert queue(w, "user_reports")["items"] == []                                      # decided: out of the queue
    assert len(notices_of(w, "blog_comment_removed")) == 1


def test_other_reasons_need_three_different_people(eng):
    w = eng
    cid = comment(w, w.people.priya, "Slightly annoying").json()["comment"]["id"]
    report(w, w.crowd[0], cid, "offensive")
    report(w, w.crowd[1], cid, "spam")
    assert queue(w, "user_reports")["items"] == []
    report(w, w.crowd[2], cid, "other")
    row = queue(w, "user_reports")["items"][0]
    assert row["report_count"] == 3 and row["report_reasons"] == {"offensive": 1, "spam": 1, "other": 1}
    assert w.db.query(BlogCommentReport).count() == 3


def test_reporting_rules(eng):
    w = eng
    cid = comment(w, w.people.priya, "Hello").json()["comment"]["id"]
    assert report(w, w.people.priya, cid).json()["code"] == "CANNOT_REPORT_OWN"
    assert report(w, None, cid).status_code == 401
    assert w.act(w.crowd[0]).post(f"{API}/comments/{cid}/report", json={"reason": "bad"}).status_code == 422
    assert report(w, w.crowd[0], "00000000-0000-0000-0000-000000000000").status_code == 404
    assert report(w, w.crowd[0], cid).status_code == 200
    assert report(w, w.crowd[0], cid, "spam").json()["code"] == "ALREADY_REPORTED"          # one report per person per comment
    assert w.db.query(BlogCommentReport).count() == 1
    assert public_comments(w, user=w.crowd[0])["items"][0]["reported_by_me"] is True
    assert public_comments(w, user=w.crowd[1])["items"][0]["reported_by_me"] is False


# ---------------------------------------------------------------- Enterprise Admin

def test_an_enterprise_admin_sees_counts_only_and_only_for_their_own_blog(eng):
    w = eng
    w.act(w.people.priya).post(f"{API}/{w.post['id']}/like")
    w.act(w.people.priya).post(f"{API}/{w.post['id']}/wishlist")
    comment(w, w.people.priya, "A published one")
    comment(w, w.crowd[0], "XXBORDER held")
    summary = w.act(w.people.asha).get(f"{API}/my/posts/{w.post['id']}/engagement")
    assert summary.status_code == 200
    data = summary.json()
    assert (data["likes_count"], data["wishlist_count"], data["comments_count"]) == (1, 1, 1)
    assert data["comments"]["published"] == 1 and data["comments"]["held_for_review"] == 1 and len(data["trend_30_days"]) == 30
    assert w.people.priya["id"] not in str(data)
    assert w.act(w.people.asha2).get(f"{API}/my/posts/{w.post['id']}/engagement").status_code == 200       # a colleague of the same business
    assert w.act(w.people.bela).get(f"{API}/my/posts/{w.post['id']}/engagement").status_code == 403
    assert w.act(w.people.priya).get(f"{API}/my/posts/{w.post['id']}/engagement").status_code == 403
    assert w.act(None).get(f"{API}/my/posts/{w.post['id']}/engagement").status_code == 401

    rows = w.act(w.people.asha).get(f"{API}/my/posts/{w.post['id']}/comments").json()["items"]
    assert len(rows) == 2 and all(r["author_id"] is None for r in rows)
    assert w.act(w.people.bela).get(f"{API}/my/posts/{w.post['id']}/comments").status_code == 403


def test_an_enterprise_admin_removes_a_comment_on_their_blog_not_another_businesss(eng):
    w = eng
    cid = comment(w, w.people.priya, "Remove me").json()["comment"]["id"]
    assert w.act(w.people.bela).delete(f"{API}/my/comments/{cid}").status_code == 403
    assert w.act(w.people.asha).delete(f"{API}/my/comments/{cid}", ).json()["status"] == "removed_by_admin"
    assert w.act(w.people.asha).delete(f"{API}/my/comments/{cid}").json()["code"] == "ALREADY_REMOVED"
    assert public_comments(w)["items"] == []
    assert len(notices_of(w, "blog_comment_removed")) == 1
    row = w.act(w.people.root).get(f"{ENG}/moderation-history", params={"comment_id": cid}).json()["items"][0]
    assert row["action"] == "enterprise_removed" and row["actor_role"] == "merchant"
    assert w.act(w.people.priya).delete(f"{API}/my/comments/{cid}").status_code == 403


def test_an_enterprise_admin_can_ask_for_a_review_of_an_ai_removal(eng):
    w = eng
    ok = comment(w, w.people.priya, "Fine comment").json()["comment"]["id"]
    assert w.act(w.people.asha).post(f"{API}/my/comments/{ok}/request-review", json={}).json()["code"] == "NOT_AI_REMOVED"
    comment(w, w.people.priya, "XXKILL this")
    removed = queue(w, "auto_removed")["items"][0]
    assert w.act(w.people.bela).post(f"{API}/my/comments/{removed['id']}/request-review", json={}).status_code == 403
    sent = w.act(w.people.asha).post(f"{API}/my/comments/{removed['id']}/request-review", json={"note": "This was a joke between friends"})
    assert sent.status_code == 200 and sent.json()["requested"] is True
    assert w.act(w.people.asha).post(f"{API}/my/comments/{removed['id']}/request-review", json={}).json()["code"] == "ALREADY_REQUESTED"
    asked = queue(w, "ea_requests")
    assert asked["counts"]["ea_requests"] == 1 and asked["items"][0]["review_request_note"] == "This was a joke between friends"
    told = notices_of(w, "blog_comment_review_requested")
    assert len(told) == 1 and len(told[0]["user_ids"]) == 2                              # both Super Admins
    assert w.act(w.people.asha).post(f"{ENG}/comments/{removed['id']}/restore", json={"reason": "Mine to restore"}).status_code == 403


# ---------------------------------------------------------------- Super Admin

def test_the_super_admin_overrides_the_ai_with_a_reason_and_it_is_recorded(eng):
    w = eng
    comment(w, w.people.priya, "XXKILL inside a joke")
    removed = queue(w, "auto_removed")["items"][0]
    assert w.act(w.people.root).post(f"{ENG}/comments/{removed['id']}/restore", json={}).status_code == 422          # a reason is mandatory
    assert w.act(w.people.root).post(f"{ENG}/comments/{removed['id']}/restore", json={"reason": "  "}).status_code == 422
    done = w.act(w.people.root).post(f"{ENG}/comments/{removed['id']}/restore", json={"reason": "Context shows it is harmless"})
    assert done.status_code == 200 and done.json()["status"] == "published"
    assert [c["body"] for c in public_comments(w)["items"]] == ["XXKILL inside a joke"]
    assert queue(w, "auto_removed")["items"] == []
    assert w.act(w.people.root).post(f"{ENG}/comments/{removed['id']}/restore", json={"reason": "again"}).json()["code"] == "NOT_RESTORABLE"
    assert len(notices_of(w, "blog_comment_restored")) == 1
    log = w.act(w.people.root).get(f"{ENG}/moderation-history", params={"comment_id": removed["id"]}).json()["items"]
    assert [e["action"] for e in log] == ["admin_restored", "created"]
    assert log[0]["reason"] == "Context shows it is harmless" and log[0]["detail"] == {"overrode_ai": True} and log[0]["actor_role"] == "super_admin"


def test_the_super_admin_removes_a_held_comment_and_a_reason_is_needed(eng):
    w = eng
    cid = comment(w, w.people.priya, "XXBORDER ugh").json()["comment"]["id"]
    assert w.act(w.people.root).post(f"{ENG}/comments/{cid}/remove", json={}).status_code == 422
    assert w.act(w.people.root).post(f"{ENG}/comments/{cid}/remove", json={"reason": "Unkind to another reader"}).json()["status"] == "removed_by_admin"
    assert queue(w, "awaiting_review")["items"] == []
    assert w.act(w.people.root).post(f"{ENG}/comments/{cid}/remove", json={"reason": "twice"}).json()["code"] == "ALREADY_REMOVED"
    assert w.act(w.people.root).post(f"{ENG}/comments/00000000-0000-0000-0000-000000000000/remove", json={"reason": "none"}).status_code == 404


def test_overview_post_detail_and_comment_search(eng):
    w = eng
    other = live_merchant_post(w, w.category, user=w.people.bela, title="Bela writes")
    w.act(w.people.priya).post(f"{API}/{w.post['id']}/like")
    w.act(w.crowd[0]).post(f"{API}/{w.post['id']}/like")
    w.act(w.crowd[0]).post(f"{API}/{w.post['id']}/wishlist")
    comment(w, w.people.priya, "Great breathing tips")
    comment(w, w.crowd[1], "XXKILL")
    comment(w, w.crowd[2], "Nice one", post=other)

    root = w.act(w.people.root)
    overview = root.get(ENG).json()
    assert overview["stats"]["blogs"] == 2 and overview["stats"]["likes"] == 2 and overview["stats"]["wishlists"] == 1
    assert overview["stats"]["comments"] == 2 and overview["stats"]["ai_removed"] == 1
    assert overview["top_blogs"][0]["post_id"] == w.post["id"] and overview["top_blogs"][0]["total"] == 4
    assert [i["title"] for i in root.get(ENG, params={"search": "Bela"}).json()["items"]] == ["Bela writes"]
    assert root.get(ENG, params={"date_from": "2999-01-01"}).json()["stats"]["likes"] == 0
    assert root.get(ENG, params={"status": "removed_by_ai"}).json()["stats"]["comments"] == 1

    detail = root.get(f"{ENG}/{w.post['id']}").json()
    assert detail["likes_count"] == 2 and detail["comments"]["removed_by_ai"] == 1 and detail["business_name"] == "Calm Clinic" and len(detail["recent_comments"]) == 2
    assert detail["recent_comments"][0]["author_id"]                                      # the Super Admin sees ids
    assert root.get(f"{ENG}/00000000-0000-0000-0000-000000000000").status_code == 404

    found = root.get(f"{ENG}/comments", params={"search": "breathing"}).json()
    assert [c["body"] for c in found["items"]] == ["Great breathing tips"] and found["items"][0]["post_title"]
    assert [c["body"] for c in root.get(f"{ENG}/comments", params={"status": "removed_by_ai"}).json()["items"]] == ["XXKILL"]
    assert len(root.get(f"{ENG}/comments", params={"post_id": other["id"]}).json()["items"]) == 1


def test_admin_routes_are_super_admin_only(eng):
    w = eng
    cid = comment(w, w.people.priya, "Hello").json()["comment"]["id"]
    urls = [("get", ENG), ("get", f"{ENG}/{w.post['id']}"), ("get", f"{ENG}/comments"), ("get", f"{ENG}/ai-moderation"), ("get", f"{ENG}/moderation-history"),
            ("post", f"{ENG}/comments/{cid}/remove"), ("post", f"{ENG}/comments/{cid}/restore")]
    for verb, url in urls:
        kwargs = {"json": {"reason": "x" * 5}} if verb == "post" else {}
        assert getattr(w.act(None), verb)(url, **kwargs).status_code == 401, url
        for user in (w.people.asha, w.people.priya):
            assert getattr(w.act(user), verb)(url, **kwargs).status_code == 403, (url, user["name"])
    assert w.act(w.people.root).get(f"{ENG}/ai-moderation", params={"queue": "bogus"}).status_code == 422
    assert w.db.query(BlogComment).filter_by(id=__import__("uuid").UUID(cid)).first().status == "published"


def test_a_blog_that_is_taken_down_closes_its_comments(eng):
    w = eng
    comment(w, w.people.priya, "Before")
    w.act(w.people.root).post(f"{API}/admin/posts/{w.post['id']}/unpublish", json={"reason": "Outdated"})
    assert w.act(None).get(f"{API}/{w.post['id']}/comments").status_code == 404
    assert comment(w, w.people.priya, "After").status_code == 404
    assert w.act(w.people.priya).post(f"{API}/{w.post['id']}/like").status_code == 404
