"""Blog engagement: likes, saved blogs, comments and replies, reports, and the Enterprise Admin / Super Admin moderation (docs/blog-engagement.md).

Only live Blogs have engagement; News never does. Everything a reader writes goes through the same AI check as Event Discussions
(app/services/event_discussion_moderation.py, the same provider and word lists):

    safe                 -> published                     (201 for a comment or reply, 200 for an edit)
    flagged              -> held_for_review               (202)  hidden until a Super Admin decides
    AI unavailable       -> pending_moderation            (202)  hidden until it can be checked again
    extreme              -> removed_by_ai                 (422)  saved for the admins, never shown, not published

Public comments carry only a display name such as "Priya S." (never a full name or an id). The text of a removed comment is kept for the admin views and the
moderation history only.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.exceptions.problem import problem
from app.models.blog_engagement_model import BlogComment, BlogCommentEvent, BlogCommentLike, BlogCommentReport, BlogLike, BlogWishlist
from app.models.blog_model import BlogPost
from app.services import notification_idempotency
from app.services.event_discussion_moderation import ModerationUnavailable, classify

logger = logging.getLogger(__name__)

SEVERE_REASONS = ("unsafe_medical", "harassment")
PENDING = ("held_for_review", "pending_moderation")
REMOVED = ("removed_by_ai", "removed_by_admin")
RESTORABLE = REMOVED + PENDING


def now() -> datetime:
    return datetime.utcnow()


def _uuid(value) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


# ---------------------------------------------------------------- the post

def _post(db: Session, post_ref, *, live: bool = True) -> BlogPost:
    """The blog by its id or its slug (the public URLs accept either)."""
    try:
        post = db.query(BlogPost).filter(BlogPost.id == _uuid(post_ref), BlogPost.is_deleted.is_(False)).first()
    except (ValueError, TypeError):
        post = db.query(BlogPost).filter(BlogPost.slug == str(post_ref), BlogPost.is_deleted.is_(False)).first()
    if post is None or (live and not post.is_live):
        raise problem(404, "That blog is not available", "POST_NOT_FOUND")
    if post.kind != "blog":
        raise problem(409, "News posts have no likes, comments or saves", "ENGAGEMENT_NOT_AVAILABLE")
    return post


# ---------------------------------------------------------------- counters on the public blog list and detail

def counts_for(db: Session, post_ids: list[UUID]) -> dict[UUID, dict]:
    out = {pid: {"likes_count": 0, "comments_count": 0, "wishlist_count": 0} for pid in post_ids}
    if not post_ids:
        return out
    for pid, n in db.query(BlogLike.post_id, func.count(BlogLike.id)).filter(BlogLike.post_id.in_(post_ids)).group_by(BlogLike.post_id).all():
        out[pid]["likes_count"] = n
    for pid, n in db.query(BlogWishlist.post_id, func.count(BlogWishlist.id)).filter(BlogWishlist.post_id.in_(post_ids)).group_by(BlogWishlist.post_id).all():
        out[pid]["wishlist_count"] = n
    for pid, n in db.query(BlogComment.post_id, func.count(BlogComment.id)).filter(BlogComment.post_id.in_(post_ids), BlogComment.status == "published").group_by(BlogComment.post_id).all():
        out[pid]["comments_count"] = n
    return out


def attach_engagement(db: Session, items, viewer_id) -> None:
    """Puts `engagement` on public list items / a detail (a Blog gets it, News gets null). `*_by_me` are null for someone not signed in."""
    from app.schemas.blog_schema import EngagementBlock

    blogs = [i for i in items if getattr(i, "kind", "blog") == "blog"]
    if not blogs:
        return
    ids = [i.id for i in blogs]
    try:
        counts = counts_for(db, ids)
        liked = saved = set()
        if viewer_id is not None:
            liked = {r[0] for r in db.query(BlogLike.post_id).filter(BlogLike.post_id.in_(ids), BlogLike.user_id == _uuid(viewer_id)).all()}
            saved = {r[0] for r in db.query(BlogWishlist.post_id).filter(BlogWishlist.post_id.in_(ids), BlogWishlist.user_id == _uuid(viewer_id)).all()}
    except Exception:        # the public blog list must keep working if the counters cannot be read (for example the migration is not applied yet)
        logger.exception("could not read blog engagement")
        db.rollback()
        return
    for item in blogs:
        item.engagement = EngagementBlock(**counts[item.id], is_liked_by_me=(item.id in liked) if viewer_id is not None else None,
                                          is_wishlisted_by_me=(item.id in saved) if viewer_id is not None else None,
                                          like_count=counts[item.id]["likes_count"], comment_count=counts[item.id]["comments_count"],
                                          liked_by_me=(item.id in liked) if viewer_id is not None else None)


# ---------------------------------------------------------------- likes and saved blogs (idempotent)

def _state(db: Session, post: BlogPost, user_id: UUID) -> dict:
    c = counts_for(db, [post.id])[post.id]
    return {"liked": db.query(BlogLike.id).filter_by(post_id=post.id, user_id=user_id).first() is not None,
            "wishlisted": db.query(BlogWishlist.id).filter_by(post_id=post.id, user_id=user_id).first() is not None,
            "likes_count": c["likes_count"], "wishlist_count": c["wishlist_count"]}


def set_like(db: Session, user: dict, post_id: UUID, on: bool) -> dict:
    uid = _uuid(user["id"])
    post = _post(db, post_id, live=on)
    existing = db.query(BlogLike).filter_by(post_id=post.id, user_id=uid).first()
    if on and existing is None:
        db.add(BlogLike(post_id=post.id, user_id=uid))
        _commit_unique(db)
    elif not on and existing is not None:
        db.delete(existing)
        db.commit()
    return _state(db, post, uid)


def set_wishlist(db: Session, user: dict, post_id: UUID, on: bool) -> dict:
    uid = _uuid(user["id"])
    post = _post(db, post_id, live=on)
    existing = db.query(BlogWishlist).filter_by(post_id=post.id, user_id=uid).first()
    if on and existing is None:
        db.add(BlogWishlist(post_id=post.id, user_id=uid))
        _commit_unique(db)
    elif not on and existing is not None:
        db.delete(existing)
        db.commit()
    return _state(db, post, uid)


def _commit_unique(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:          # the same person twice at once: the other request won, which is the answer wanted
        db.rollback()


def my_saved(db: Session, user: dict, page: int, page_size: int) -> dict:
    from app.repository.query_utils import build_pagination_meta
    from app.schemas.blog_schema import BlogListItemResponse
    from app.services.blog_service import _public_dict

    uid = _uuid(user["id"])
    query = (db.query(BlogPost, BlogWishlist.saved_at).join(BlogWishlist, BlogWishlist.post_id == BlogPost.id)
             .filter(BlogWishlist.user_id == uid, BlogPost.is_live.is_(True), BlogPost.is_deleted.is_(False), BlogPost.kind == "blog")
             .order_by(BlogWishlist.saved_at.desc(), BlogWishlist.id))
    total = query.count()
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    items = [BlogListItemResponse.model_validate(_public_dict(post)) for post, _saved in rows]
    attach_engagement(db, items, uid)
    return {"items": [{**item.model_dump(mode="json"), "saved_at": saved.isoformat()} for item, (_p, saved) in zip(items, rows)],
            "pagination": build_pagination_meta(total, page, page_size)}


# ---------------------------------------------------------------- moderation

def judge(text: str) -> dict:
    try:
        verdict = classify(text)
    except ModerationUnavailable:
        return {"status": "pending_moderation", "reason": "The AI check was not available", "labels": [], "provider": "none"}
    status = {"safe": "published", "flagged": "held_for_review", "extreme": "removed_by_ai"}[verdict.label]
    return {"status": status, "reason": verdict.reason, "labels": list(verdict.categories or []), "provider": verdict.provider}


def _log(db: Session, comment: BlogComment, action: str, actor_id, actor_role: str, *, from_status=None, to_status=None, reason=None, snapshot=False, detail=None) -> None:
    db.add(BlogCommentEvent(comment_id=comment.id, post_id=comment.post_id, action=action, from_status=from_status, to_status=to_status, actor_id=actor_id,
                            actor_role=actor_role, reason=reason, body_snapshot=comment.body if snapshot else None, detail=detail))


def _clean_text(body: str) -> str:
    text = " ".join((body or "").split())
    if not text:
        raise problem(422, "Write something first", "COMMENT_EMPTY")
    if len(text) > settings.BLOG_COMMENT_MAX_CHARS:
        raise problem(422, f"A comment can have at most {settings.BLOG_COMMENT_MAX_CHARS} characters", "COMMENT_TOO_LONG", max_chars=settings.BLOG_COMMENT_MAX_CHARS)
    return text


def _rate_limit(db: Session, uid: UUID) -> None:
    window = now() - timedelta(seconds=settings.BLOG_COMMENT_RATE_WINDOW_SECONDS)
    sent = db.query(func.count(BlogCommentEvent.id)).filter(BlogCommentEvent.actor_id == uid, BlogCommentEvent.actor_role == "customer",
                                                            BlogCommentEvent.action.in_(("created", "author_edited", "edit_rejected")), BlogCommentEvent.created_at >= window).scalar() or 0
    if sent >= settings.BLOG_COMMENT_RATE_LIMIT:
        raise problem(429, "You are posting too fast. Wait a moment and try again", "RATE_LIMITED", retry_after_seconds=settings.BLOG_COMMENT_RATE_WINDOW_SECONDS)


def public_name(user: dict) -> str:
    """"Priya Sharma" -> "Priya S.": a first name and the initial of the last. Never an e-mail address."""
    from app.services.group_chat_common import default_display_name

    return default_display_name(str(user.get("name") or "")) or "Reader"


def _outcome(comment: BlogComment) -> tuple[str, str]:
    return {
        "published": ("published", "Your comment is posted."),
        "held_for_review": ("held_for_review", "Your comment is waiting for a moderator before it is shown."),
        "pending_moderation": ("pending_moderation", "Your comment is waiting to be checked and will be shown once it is."),
        "removed_by_ai": ("removed_by_ai", "This comment goes against our community guidelines and was not posted."),
    }[comment.status]


def create_comment(db: Session, user: dict, post_id: UUID, body: str, parent_id: UUID | None) -> tuple[BlogComment, int]:
    uid = _uuid(user["id"])
    text = _clean_text(body)
    post = _post(db, post_id)
    parent = None
    if parent_id is not None:
        parent = db.query(BlogComment).filter(BlogComment.id == parent_id, BlogComment.post_id == post.id).first()
        if parent is None or parent.status != "published":
            raise problem(404, "The comment you are replying to is not available", "PARENT_NOT_AVAILABLE")
        depth = _depth(db, parent)                                     # a top-level comment is depth 1
        while depth >= max(settings.BLOG_COMMENT_MAX_DEPTH, 1) and parent.parent_id is not None:
            parent, depth = db.get(BlogComment, parent.parent_id), depth - 1      # too deep: it joins the deepest level allowed
        if settings.BLOG_COMMENT_MAX_DEPTH <= 1:
            raise problem(409, "Replies are switched off", "REPLIES_DISABLED")
    _rate_limit(db, uid)
    verdict = judge(text)
    comment = BlogComment(post_id=post.id, tenant_id=post.tenant_id, parent_id=parent.id if parent else None, author_id=uid, author_name=public_name(user), body=text,
                          status=verdict["status"], moderation_reason=verdict["reason"] if verdict["status"] != "published" else None,
                          moderation_labels=verdict["labels"] or None)
    if comment.status == "removed_by_ai":
        comment.removed_by_role, comment.removed_at, comment.removed_reason = "system", now(), verdict["reason"]
    db.add(comment)
    db.flush()
    _log(db, comment, "created", uid, "customer", to_status=comment.status, reason=verdict["reason"], snapshot=comment.status != "published",
         detail={"provider": verdict["provider"], "labels": verdict["labels"], "is_reply": parent is not None})
    db.commit()
    db.refresh(comment)
    return comment, {"published": 201, "held_for_review": 202, "pending_moderation": 202, "removed_by_ai": 422}[comment.status]


def edit_comment(db: Session, user: dict, comment_id: UUID, body: str) -> tuple[BlogComment, int]:
    uid = _uuid(user["id"])
    text = _clean_text(body)
    comment = db.query(BlogComment).filter(BlogComment.id == comment_id, BlogComment.author_id == uid).first()
    if comment is None or comment.status in ("deleted_by_author",):
        raise problem(404, "Comment not found", "COMMENT_NOT_FOUND")
    if comment.status in REMOVED:
        raise problem(409, "A removed comment cannot be edited", "COMMENT_NOT_EDITABLE")
    _post(db, comment.post_id)
    _rate_limit(db, uid)
    before = comment.status
    verdict = judge(text)
    if verdict["status"] == "removed_by_ai":          # the edit is refused: the comment keeps what it said, the refused text is kept for the admins
        db.add(BlogCommentEvent(comment_id=comment.id, post_id=comment.post_id, action="edit_rejected", from_status=before, to_status=before, actor_id=uid, actor_role="customer",
                                reason=verdict["reason"], body_snapshot=text, detail={"provider": verdict["provider"], "labels": verdict["labels"]}))
        db.commit()
        return comment, 422
    comment.body, comment.is_edited, comment.edited_at, comment.status = text, True, now(), verdict["status"]
    comment.moderation_reason = verdict["reason"] if verdict["status"] != "published" else None
    comment.moderation_labels = verdict["labels"] or None
    _log(db, comment, "author_edited", uid, "customer", from_status=before, to_status=comment.status, reason=verdict["reason"], snapshot=comment.status != "published",
         detail={"provider": verdict["provider"]})
    db.commit()
    db.refresh(comment)
    return comment, 200 if comment.status == "published" else 202


def delete_own_comment(db: Session, user: dict, comment_id: UUID) -> dict:
    uid = _uuid(user["id"])
    comment = db.query(BlogComment).filter(BlogComment.id == comment_id, BlogComment.author_id == uid).first()
    if comment is None:
        raise problem(404, "Comment not found", "COMMENT_NOT_FOUND")
    if comment.status != "deleted_by_author":
        before = comment.status
        comment.status = "deleted_by_author"
        _log(db, comment, "author_deleted", uid, "customer", from_status=before, to_status="deleted_by_author")
        db.commit()
    return {"deleted": True}


# ---------------------------------------------------------------- reading comments

def _visible(viewer_id):
    clause = BlogComment.status == "published"
    return or_(clause, (BlogComment.author_id == viewer_id) & BlogComment.status.in_(PENDING)) if viewer_id is not None else clause


def _depth(db: Session, comment: BlogComment) -> int:
    depth, seen = 1, {comment.id}
    while comment.parent_id is not None and comment.parent_id not in seen and depth < 50:
        seen.add(comment.parent_id)
        comment = db.get(BlogComment, comment.parent_id)
        if comment is None:
            break
        depth += 1
    return depth


def _comment_out(c: BlogComment, viewer_id, reported: set, replies=None, placeholder=False, likes: dict | None = None, liked: set | None = None) -> dict:
    mine = viewer_id is not None and c.author_id == viewer_id
    status = "unavailable" if placeholder else ("published" if c.status == "published" else "pending_review")
    name = None if placeholder else c.author_name
    return {
        "id": c.id, "post_id": c.post_id, "parent_id": c.parent_id, "author": {"name": name, "is_me": mine and not placeholder}, "author_name": name,
        "body": None if placeholder else c.body, "status": status, "created_at": c.created_at, "is_edited": bool(c.is_edited) and not placeholder,
        "edited_at": None if placeholder else c.edited_at, "can_edit": mine and not placeholder, "can_delete": mine and not placeholder,
        "reported_by_me": c.id in reported, "like_count": (likes or {}).get(c.id, 0),
        "liked_by_me": (c.id in (liked or set())) if viewer_id is not None else None,
        "reply_count": len(replies or []), "replies": replies or [],
    }


def _tree(db: Session, post: BlogPost, viewer_id) -> tuple[list[dict], dict]:
    """Every thread of the post, newest top-level comment first, replies oldest first at every level. A comment the viewer may not see is left out, unless it
    has replies the viewer may see: then it stays as a placeholder (status unavailable, no text, no author). Returns (top-level nodes, node by comment id)."""
    rows = db.query(BlogComment).filter(BlogComment.post_id == post.id).all()
    children: dict = defaultdict(list)
    for c in rows:
        children[c.parent_id].append(c)
    ids = [c.id for c in rows]
    likes = dict(db.query(BlogCommentLike.comment_id, func.count(BlogCommentLike.id)).filter(BlogCommentLike.comment_id.in_(ids))
                 .group_by(BlogCommentLike.comment_id).all()) if ids else {}
    liked = {r[0] for r in db.query(BlogCommentLike.comment_id).filter(BlogCommentLike.comment_id.in_(ids), BlogCommentLike.user_id == viewer_id).all()} if viewer_id and ids else set()
    reported = {r[0] for r in db.query(BlogCommentReport.comment_id).filter(BlogCommentReport.comment_id.in_(ids), BlogCommentReport.reporter_id == viewer_id).all()} if viewer_id and ids else set()
    nodes: dict = {}

    def sees(c: BlogComment) -> bool:
        return c.status == "published" or (viewer_id is not None and c.author_id == viewer_id and c.status in PENDING)

    def build(c: BlogComment, depth: int):
        kids = [n for n in (build(k, depth + 1) for k in sorted(children[c.id], key=lambda k: (k.created_at, str(k.id)))) if n is not None] if depth < 50 else []
        if not sees(c) and not kids:
            return None
        node = _comment_out(c, viewer_id, reported, kids, placeholder=not sees(c), likes=likes, liked=liked)
        nodes[c.id] = node
        return node

    tops = sorted(children[None], key=lambda k: (k.created_at, str(k.id)), reverse=True)
    return [n for n in (build(c, 1) for c in tops) if n is not None], nodes


def list_comments(db: Session, post_ref, viewer: dict | None, page: int, page_size: int) -> dict:
    from app.repository.query_utils import build_pagination_meta

    post = _post(db, post_ref)
    viewer_id = _uuid(viewer["id"]) if viewer and viewer.get("id") else None
    threads, _nodes = _tree(db, post, viewer_id)
    start = (page - 1) * page_size
    return {"items": threads[start:start + page_size], "pagination": build_pagination_meta(len(threads), page, page_size),
            "comments_count": counts_for(db, [post.id])[post.id]["comments_count"]}


def list_replies(db: Session, post_ref, comment_id: UUID, viewer: dict | None, page: int, page_size: int) -> dict:
    """The direct replies of one comment (each with its own nested replies), oldest first: for loading a long thread a page at a time."""
    from app.repository.query_utils import build_pagination_meta

    post = _post(db, post_ref)
    viewer_id = _uuid(viewer["id"]) if viewer and viewer.get("id") else None
    _threads, nodes = _tree(db, post, viewer_id)
    node = nodes.get(comment_id)
    if node is None:
        raise problem(404, "Comment not found", "COMMENT_NOT_FOUND")
    replies = node["replies"]
    start = (page - 1) * page_size
    return {"items": replies[start:start + page_size], "pagination": build_pagination_meta(len(replies), page, page_size)}


def comment_out(db: Session, comment: BlogComment, viewer: dict) -> dict:
    viewer_id = _uuid(viewer["id"])
    count = db.query(func.count(BlogCommentLike.id)).filter(BlogCommentLike.comment_id == comment.id).scalar() or 0
    mine = db.query(BlogCommentLike.id).filter_by(comment_id=comment.id, user_id=viewer_id).first() is not None
    return _comment_out(comment, viewer_id, set(), likes={comment.id: count}, liked={comment.id} if mine else set())


# ---------------------------------------------------------------- the mobile contract: likes summary, comment likes

def like_summary(db: Session, viewer: dict | None, post_ref) -> dict:
    post = _post(db, post_ref)
    count = counts_for(db, [post.id])[post.id]["likes_count"]
    if not viewer or not viewer.get("id"):
        return {"like_count": count, "liked_by_me": None}
    mine = db.query(BlogLike.id).filter_by(post_id=post.id, user_id=_uuid(viewer["id"])).first() is not None
    return {"like_count": count, "liked_by_me": mine}


def set_like_summary(db: Session, user: dict, post_ref, on: bool) -> dict:
    state = set_like(db, user, post_ref, on)
    return {"like_count": state["likes_count"], "liked_by_me": state["liked"]}


def set_comment_like(db: Session, user: dict, post_ref, comment_id: UUID, on: bool) -> dict:
    uid = _uuid(user["id"])
    post = _post(db, post_ref, live=on)
    comment = db.query(BlogComment).filter(BlogComment.id == comment_id, BlogComment.post_id == post.id).first()
    if comment is None or (on and comment.status != "published"):
        raise problem(404, "Comment not found", "COMMENT_NOT_FOUND")
    existing = db.query(BlogCommentLike).filter_by(comment_id=comment.id, user_id=uid).first()
    if on and existing is None:
        db.add(BlogCommentLike(comment_id=comment.id, user_id=uid))
        _commit_unique(db)
    elif not on and existing is not None:
        db.delete(existing)
        db.commit()
    count = db.query(func.count(BlogCommentLike.id)).filter(BlogCommentLike.comment_id == comment.id).scalar() or 0
    return {"id": comment.id, "like_count": count, "liked_by_me": on}


# ---------------------------------------------------------------- notifications

def _notify(db: Session, users, category: str, title: str, body: str, comment: BlogComment, key: str, reason: str | None = None) -> None:
    ids = []
    for u in dict.fromkeys(users or []):
        try:
            ids.append(_uuid(u))
        except (ValueError, TypeError):
            continue
    if not ids:
        return
    post = db.get(BlogPost, comment.post_id)
    data = {"category": category, "entity_type": "blog_comment", "entity_id": comment.id, "comment_id": comment.id, "post_id": comment.post_id,
            "post_slug": getattr(post, "slug", None), "status": comment.status, "reason": reason}
    data = {k: str(v) for k, v in data.items() if v is not None and v != ""}
    try:
        if not notification_idempotency.claim(db, key):
            return
        from app.services.notification_service import create_automatic_notification

        create_automatic_notification(db, title=title, message=body, category=category, user_ids=ids, tenant_id=comment.tenant_id, metadata=data, channels=["in_app", "push"])
    except Exception:
        logger.exception("blog comment notification %s failed", category)
        try:
            db.rollback()
            notification_idempotency.release(db, key)
        except Exception:
            pass


def _stamp(comment: BlogComment) -> str:
    return (comment.updated_at or comment.created_at).replace(microsecond=0).isoformat()


def _tell_author_removed(db: Session, comment: BlogComment, reason: str | None) -> None:
    _notify(db, [comment.author_id], "blog_comment_removed", "Comment removed", "Your comment on a blog was removed because it goes against our community guidelines.",
            comment, f"blog_comment_removed:{comment.id}:{_stamp(comment)}", reason)


def _tell_platform_admins(db: Session, comment: BlogComment, category: str, title: str, body: str) -> None:
    try:
        from app.services.event_notification_service import resolve_platform_admin_user_ids

        users = resolve_platform_admin_user_ids()
    except Exception:
        logger.debug("could not list the Super Admins", exc_info=True)
        return
    _notify(db, users, category, title, body, comment, f"{category}:{comment.id}:{_stamp(comment)}")


# ---------------------------------------------------------------- reports

def report_comment(db: Session, user: dict, comment_id: UUID, reason: str, note: str | None) -> dict:
    uid = _uuid(user["id"])
    comment = db.get(BlogComment, comment_id)
    if comment is None or comment.status != "published":
        raise problem(404, "Comment not found", "COMMENT_NOT_FOUND")
    _post(db, comment.post_id)
    if comment.author_id == uid:
        raise problem(409, "You cannot report your own comment", "CANNOT_REPORT_OWN")
    if db.query(BlogCommentReport.id).filter_by(comment_id=comment.id, reporter_id=uid).first() is not None:
        raise problem(409, "You already reported this comment", "ALREADY_REPORTED")
    db.add(BlogCommentReport(comment_id=comment.id, reporter_id=uid, reason=reason, note=(note or "").strip() or None))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise problem(409, "You already reported this comment", "ALREADY_REPORTED")
    triggered = False
    if comment.report_triggered_at is None:
        severe = db.query(func.count(BlogCommentReport.id)).filter(BlogCommentReport.comment_id == comment.id, BlogCommentReport.reason.in_(SEVERE_REASONS)).scalar() or 0
        other = db.query(func.count(BlogCommentReport.id)).filter(BlogCommentReport.comment_id == comment.id, BlogCommentReport.reason.notin_(SEVERE_REASONS)).scalar() or 0
        if severe >= settings.BLOG_REPORT_THRESHOLD_SEVERE or other >= settings.BLOG_REPORT_THRESHOLD_OTHER:
            triggered = True
            comment.report_triggered_at = now()
            _log(db, comment, "report_threshold_reached", uid, "system", from_status=comment.status, to_status=comment.status, detail={"severe_reports": severe, "other_reports": other})
            _re_evaluate(db, comment)
    db.commit()
    db.refresh(comment)
    if triggered and comment.status == "removed_by_ai":
        _tell_author_removed(db, comment, comment.removed_reason)
    return {"reported": True}


def _re_evaluate(db: Session, comment: BlogComment) -> None:
    """Judge the text again after enough reports: severe removes it, borderline holds it; otherwise it stays up and waits in the Reported queue."""
    verdict = judge(comment.body)
    before = comment.status
    if verdict["status"] == "removed_by_ai":
        comment.status, comment.removed_by_role, comment.removed_at, comment.removed_reason = "removed_by_ai", "system", now(), verdict["reason"]
        comment.moderation_reason, comment.moderation_labels = verdict["reason"], verdict["labels"] or None
        _log(db, comment, "ai_removed_after_report", None, "system", from_status=before, to_status=comment.status, reason=verdict["reason"], snapshot=True,
             detail={"provider": verdict["provider"], "labels": verdict["labels"]})
    elif verdict["status"] == "held_for_review":
        comment.status, comment.moderation_reason, comment.moderation_labels = "held_for_review", verdict["reason"], verdict["labels"] or None
        _log(db, comment, "ai_held_after_report", None, "system", from_status=before, to_status=comment.status, reason=verdict["reason"], snapshot=True,
             detail={"provider": verdict["provider"], "labels": verdict["labels"]})
    else:
        _log(db, comment, "ai_rechecked_after_report", None, "system", from_status=before, to_status=before,
             reason="The AI check was not available" if verdict["status"] == "pending_moderation" else "The AI found nothing wrong", detail={"provider": verdict["provider"]})


def recheck_pending(db: Session, limit: int = 100) -> int:
    """Judge again the comments that waited because the AI was down. Safe ones are published; a verdict of flagged or extreme moves them on."""
    done = 0
    for comment in db.query(BlogComment).filter(BlogComment.status == "pending_moderation").order_by(BlogComment.created_at).limit(limit).all():
        verdict = judge(comment.body)
        if verdict["status"] == "pending_moderation":
            break                                          # still down
        before = comment.status
        comment.status, comment.moderation_reason, comment.moderation_labels = verdict["status"], verdict["reason"] if verdict["status"] != "published" else None, verdict["labels"] or None
        if verdict["status"] == "removed_by_ai":
            comment.removed_by_role, comment.removed_at, comment.removed_reason = "system", now(), verdict["reason"]
        _log(db, comment, "ai_checked_late", None, "system", from_status=before, to_status=comment.status, reason=verdict["reason"], snapshot=comment.status != "published")
        db.commit()
        done += 1
    return done


# ---------------------------------------------------------------- Enterprise Admin (aggregates and their own blogs' comments only)

def _own_post(db: Session, actor, post_id: UUID) -> BlogPost:
    post = db.query(BlogPost).filter(BlogPost.id == post_id, BlogPost.is_deleted.is_(False)).first()
    if post is None:
        raise problem(404, "Blog not found", "POST_NOT_FOUND")
    if post.tenant_id is None or post.tenant_id != actor.tenant_id:
        raise problem(403, "That blog belongs to another business", "FORBIDDEN")
    return post


def _trend(db: Session, post_id: UUID, days: int = 30) -> list[dict]:
    since = (now() - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    series: dict[str, dict] = {(since + timedelta(days=n)).date().isoformat(): {"date": (since + timedelta(days=n)).date().isoformat(), "likes": 0, "comments": 0, "saves": 0} for n in range(days)}
    for model, column, key, extra in ((BlogLike, BlogLike.created_at, "likes", ()), (BlogWishlist, BlogWishlist.saved_at, "saves", ()),
                                      (BlogComment, BlogComment.created_at, "comments", (BlogComment.status == "published",))):
        for stamp in db.query(column).filter(model.post_id == post_id, column >= since, *extra).all():
            day = stamp[0].date().isoformat()
            if day in series:
                series[day][key] += 1
    return list(series.values())


def engagement_summary(db: Session, post: BlogPost) -> dict:
    c = counts_for(db, [post.id])[post.id]
    by_status = dict(db.query(BlogComment.status, func.count(BlogComment.id)).filter(BlogComment.post_id == post.id).group_by(BlogComment.status).all())
    open_reports = db.query(func.count(BlogComment.id)).filter(BlogComment.post_id == post.id, BlogComment.report_triggered_at.isnot(None), BlogComment.report_resolved_at.is_(None)).scalar() or 0
    return {"post_id": post.id, "title": post.title, "slug": post.slug, "kind": post.kind, "is_live": post.is_live, **c,
            "comments": {"published": by_status.get("published", 0), "held_for_review": by_status.get("held_for_review", 0), "pending_moderation": by_status.get("pending_moderation", 0),
                         "removed_by_ai": by_status.get("removed_by_ai", 0), "removed_by_admin": by_status.get("removed_by_admin", 0), "deleted_by_author": by_status.get("deleted_by_author", 0)},
            "open_reports": open_reports, "view_count": post.view_count, "trend_30_days": _trend(db, post.id)}


def ea_engagement(db: Session, actor, post_id: UUID) -> dict:
    """Counts only: nobody's identity."""
    return engagement_summary(db, _own_post(db, actor, post_id))


def _moderation_row(db: Session, c: BlogComment, *, show_author_id: bool, reports: dict | None = None, posts: dict | None = None) -> dict:
    post = (posts or {}).get(c.post_id) or db.get(BlogPost, c.post_id)
    reasons = (reports or {}).get(c.id, {})
    return {
        "id": c.id, "post_id": c.post_id, "post_title": getattr(post, "title", None), "post_slug": getattr(post, "slug", None), "business_name": getattr(post, "business_name", None),
        "parent_id": c.parent_id, "author_id": c.author_id if show_author_id else None, "author_name": c.author_name, "body": c.body, "status": c.status,
        "is_edited": bool(c.is_edited), "moderation_reason": c.moderation_reason, "moderation_labels": c.moderation_labels, "removed_by_role": c.removed_by_role,
        "removed_reason": c.removed_reason, "report_count": sum(reasons.values()), "report_reasons": reasons, "review_requested_at": c.review_requested_at,
        "review_request_note": c.review_request_note, "created_at": c.created_at, "updated_at": c.updated_at,
    }


def _report_breakdown(db: Session, ids: list[UUID]) -> dict[UUID, dict]:
    out: dict[UUID, dict] = defaultdict(dict)
    if ids:
        for cid, reason, n in db.query(BlogCommentReport.comment_id, BlogCommentReport.reason, func.count(BlogCommentReport.id)).filter(BlogCommentReport.comment_id.in_(ids)).group_by(
                BlogCommentReport.comment_id, BlogCommentReport.reason).all():
            out[cid][reason] = n
    return out


def _page(db: Session, query, page: int, page_size: int, *, show_author_id: bool) -> dict:
    from app.repository.query_utils import build_pagination_meta

    total = query.count()
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    reports = _report_breakdown(db, [r.id for r in rows])
    posts = {p.id: p for p in db.query(BlogPost).filter(BlogPost.id.in_({r.post_id for r in rows})).all()} if rows else {}
    return {"items": [_moderation_row(db, r, show_author_id=show_author_id, reports=reports, posts=posts) for r in rows], "pagination": build_pagination_meta(total, page, page_size)}


def ea_comments(db: Session, actor, post_id: UUID, status: str | None, page: int, page_size: int) -> dict:
    post = _own_post(db, actor, post_id)
    query = db.query(BlogComment).filter(BlogComment.post_id == post.id)
    if status:
        query = query.filter(BlogComment.status.in_([s.strip() for s in status.split(",") if s.strip()]))
    return _page(db, query.order_by(BlogComment.created_at.desc()), page, page_size, show_author_id=False)


def _own_comment(db: Session, actor, comment_id: UUID) -> BlogComment:
    comment = db.get(BlogComment, comment_id)
    if comment is None:
        raise problem(404, "Comment not found", "COMMENT_NOT_FOUND")
    _own_post(db, actor, comment.post_id)                 # 403 when it is on another business's blog
    return comment


def ea_remove(db: Session, actor, comment_id: UUID, reason: str | None) -> dict:
    comment = _own_comment(db, actor, comment_id)
    if comment.status in ("removed_by_admin", "deleted_by_author"):
        raise problem(409, f"This comment is already {comment.status.replace('_', ' ')}", "ALREADY_REMOVED")
    return _remove(db, comment, UUID(str(actor.id)), "merchant", " ".join((reason or "").split()) or None)


def ea_request_review(db: Session, actor, comment_id: UUID, note: str | None) -> dict:
    comment = _own_comment(db, actor, comment_id)
    if comment.status != "removed_by_ai":
        raise problem(409, "Only a comment the AI removed can be sent for review", "NOT_AI_REMOVED")
    if comment.review_requested_at is not None and comment.review_resolved_at is None:
        raise problem(409, "A review of this comment is already requested", "ALREADY_REQUESTED")
    comment.review_requested_at, comment.review_requested_by, comment.review_request_note, comment.review_resolved_at = now(), UUID(str(actor.id)), (note or "").strip() or None, None
    _log(db, comment, "ea_review_requested", UUID(str(actor.id)), "merchant", from_status=comment.status, to_status=comment.status, reason=comment.review_request_note, snapshot=True)
    db.commit()
    db.refresh(comment)
    _tell_platform_admins(db, comment, "blog_comment_review_requested", "Comment review requested", "An Enterprise Admin asked you to look at a comment the AI removed.")
    return {"requested": True, "comment_id": comment.id}


# ---------------------------------------------------------------- Super Admin

def _remove(db: Session, comment: BlogComment, actor_id: UUID, role: str, reason: str | None) -> dict:
    before = comment.status
    comment.status, comment.removed_by, comment.removed_by_role, comment.removed_reason, comment.removed_at = "removed_by_admin", actor_id, role, reason, now()
    _resolve_flags(comment)
    _log(db, comment, "admin_removed" if role == "super_admin" else "enterprise_removed", actor_id, role, from_status=before, to_status=comment.status, reason=reason, snapshot=True)
    db.commit()
    db.refresh(comment)
    _tell_author_removed(db, comment, reason)
    return {"id": comment.id, "status": comment.status}


def _resolve_flags(comment: BlogComment) -> None:
    stamp = now()
    if comment.report_triggered_at is not None and comment.report_resolved_at is None:
        comment.report_resolved_at = stamp
    if comment.review_requested_at is not None and comment.review_resolved_at is None:
        comment.review_resolved_at = stamp


def admin_comment(db: Session, comment_id: UUID) -> BlogComment:
    comment = db.get(BlogComment, comment_id)
    if comment is None:
        raise problem(404, "Comment not found", "COMMENT_NOT_FOUND")
    return comment


def admin_remove(db: Session, actor, comment_id: UUID, reason: str) -> dict:
    comment = admin_comment(db, comment_id)
    if comment.status in ("removed_by_admin", "deleted_by_author"):
        raise problem(409, f"This comment is already {comment.status.replace('_', ' ')}", "ALREADY_REMOVED")
    return _remove(db, comment, UUID(str(actor.id)), "super_admin", " ".join(reason.split()))


def admin_restore(db: Session, actor, comment_id: UUID, reason: str) -> dict:
    comment = admin_comment(db, comment_id)
    if comment.status not in RESTORABLE:
        raise problem(409, "Only a removed or waiting comment can be restored" if comment.status != "published" else "This comment is already published", "NOT_RESTORABLE")
    before = comment.status
    clean = " ".join(reason.split())
    comment.status, comment.restored_at, comment.moderation_reason = "published", now(), None
    comment.removed_by = comment.removed_by_role = comment.removed_reason = comment.removed_at = None
    _resolve_flags(comment)
    _log(db, comment, "admin_restored", UUID(str(actor.id)), "super_admin", from_status=before, to_status="published", reason=clean, snapshot=True,
         detail={"overrode_ai": before == "removed_by_ai"})
    db.commit()
    db.refresh(comment)
    _notify(db, [comment.author_id], "blog_comment_restored", "Comment restored", "Your comment was reviewed and is visible again.", comment, f"blog_comment_restored:{comment.id}:{_stamp(comment)}")
    return {"id": comment.id, "status": comment.status}


QUEUES = {
    "auto_removed": lambda: BlogComment.status == "removed_by_ai",
    "awaiting_review": lambda: BlogComment.status.in_(PENDING),
    "user_reports": lambda: (BlogComment.report_triggered_at.isnot(None)) & (BlogComment.report_resolved_at.is_(None)),
    "ea_requests": lambda: (BlogComment.review_requested_at.isnot(None)) & (BlogComment.review_resolved_at.is_(None)),
}


def ai_moderation(db: Session, queue: str, search: str | None, page: int, page_size: int) -> dict:
    counts = {name: db.query(func.count(BlogComment.id)).filter(make()).scalar() or 0 for name, make in QUEUES.items()}
    query = db.query(BlogComment).filter(QUEUES[queue]())
    if search and search.strip():
        like = f"%{search.strip().lower()}%"
        query = query.filter(or_(func.lower(BlogComment.body).like(like), func.lower(BlogComment.author_name).like(like),
                                 BlogComment.post_id.in_(db.query(BlogPost.id).filter(or_(func.lower(BlogPost.title).like(like), func.lower(BlogPost.business_name).like(like))))))
    order = BlogComment.review_requested_at.asc() if queue == "ea_requests" else BlogComment.report_triggered_at.asc() if queue == "user_reports" else BlogComment.created_at.asc()
    return {"queue": queue, "counts": counts, **_page(db, query.order_by(order), page, page_size, show_author_id=True)}


def admin_comments(db: Session, *, search: str | None, status: str | None, post_id: UUID | None, tenant_id: UUID | None, page: int, page_size: int) -> dict:
    query = db.query(BlogComment)
    if status:
        query = query.filter(BlogComment.status.in_([s.strip() for s in status.split(",") if s.strip()]))
    if post_id:
        query = query.filter(BlogComment.post_id == post_id)
    if tenant_id:
        query = query.filter(BlogComment.tenant_id == tenant_id)
    if search and search.strip():
        like = f"%{search.strip().lower()}%"
        query = query.filter(or_(func.lower(BlogComment.body).like(like), func.lower(BlogComment.author_name).like(like),
                                 BlogComment.post_id.in_(db.query(BlogPost.id).filter(or_(func.lower(BlogPost.title).like(like), func.lower(BlogPost.business_name).like(like))))))
    return _page(db, query.order_by(BlogComment.created_at.desc()), page, page_size, show_author_id=True)


def admin_overview(db: Session, *, search: str | None, status: str | None, date_from, date_to, page: int, page_size: int) -> dict:
    from app.repository.query_utils import build_pagination_meta

    def within(column):
        clauses = []
        if date_from:
            clauses.append(column >= datetime.combine(date_from, datetime.min.time()))
        if date_to:
            clauses.append(column < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
        return clauses

    statuses = [s.strip() for s in (status or "").split(",") if s.strip()]
    comment_filters = within(BlogComment.created_at) + ([BlogComment.status.in_(statuses)] if statuses else [BlogComment.status == "published"])
    posts = db.query(BlogPost).filter(BlogPost.kind == "blog", BlogPost.is_deleted.is_(False))
    if search and search.strip():
        like = f"%{search.strip().lower()}%"
        posts = posts.filter(or_(func.lower(BlogPost.title).like(like), func.lower(BlogPost.business_name).like(like), func.lower(BlogPost.author_name).like(like)))
    ids = [p.id for p in posts.all()]
    likes = dict(db.query(BlogLike.post_id, func.count(BlogLike.id)).filter(BlogLike.post_id.in_(ids), *within(BlogLike.created_at)).group_by(BlogLike.post_id).all()) if ids else {}
    saves = dict(db.query(BlogWishlist.post_id, func.count(BlogWishlist.id)).filter(BlogWishlist.post_id.in_(ids), *within(BlogWishlist.saved_at)).group_by(BlogWishlist.post_id).all()) if ids else {}
    comments = dict(db.query(BlogComment.post_id, func.count(BlogComment.id)).filter(BlogComment.post_id.in_(ids), *comment_filters).group_by(BlogComment.post_id).all()) if ids else {}
    rows = [{"post_id": p.id, "title": p.title, "slug": p.slug, "business_name": p.business_name, "is_live": p.is_live, "likes_count": likes.get(p.id, 0),
             "comments_count": comments.get(p.id, 0), "wishlist_count": saves.get(p.id, 0)} for p in posts.all()]
    for r in rows:
        r["total"] = r["likes_count"] + r["comments_count"] + r["wishlist_count"]
    rows.sort(key=lambda r: (-r["total"], r["title"] or ""))
    start = (page - 1) * page_size
    queue_counts = {name: db.query(func.count(BlogComment.id)).filter(make()).scalar() or 0 for name, make in QUEUES.items()}
    return {
        "stats": {"blogs": len(rows), "likes": sum(likes.values()), "wishlists": sum(saves.values()), "comments": sum(comments.values()),
                  "ai_removed": queue_counts["auto_removed"], "awaiting_review": queue_counts["awaiting_review"], "user_reports": queue_counts["user_reports"],
                  "ea_requests": queue_counts["ea_requests"]},
        "top_blogs": [r for r in rows[:10] if r["total"] > 0], "items": rows[start:start + page_size], "pagination": build_pagination_meta(len(rows), page, page_size),
    }


def admin_post_detail(db: Session, post_id: UUID) -> dict:
    post = db.query(BlogPost).filter(BlogPost.id == post_id, BlogPost.is_deleted.is_(False)).first()
    if post is None:
        raise problem(404, "Blog not found", "POST_NOT_FOUND")
    summary = engagement_summary(db, post)
    recent = _page(db, db.query(BlogComment).filter(BlogComment.post_id == post.id).order_by(BlogComment.created_at.desc()), 1, 10, show_author_id=True)["items"]
    return {**summary, "business_name": post.business_name, "tenant_id": post.tenant_id, "recent_comments": recent}


def moderation_history(db: Session, *, comment_id: UUID | None, post_id: UUID | None, action: str | None, page: int, page_size: int) -> dict:
    from app.repository.query_utils import build_pagination_meta

    query = db.query(BlogCommentEvent)
    if comment_id:
        query = query.filter(BlogCommentEvent.comment_id == comment_id)
    if post_id:
        query = query.filter(BlogCommentEvent.post_id == post_id)
    if action:
        query = query.filter(BlogCommentEvent.action.in_([a.strip() for a in action.split(",") if a.strip()]))
    query = query.order_by(BlogCommentEvent.created_at.desc(), BlogCommentEvent.id)
    total = query.count()
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": [{"id": e.id, "comment_id": e.comment_id, "post_id": e.post_id, "action": e.action, "from_status": e.from_status, "to_status": e.to_status,
                       "actor_id": e.actor_id, "actor_role": e.actor_role, "reason": e.reason, "body_snapshot": e.body_snapshot, "detail": e.detail, "created_at": e.created_at}
                      for e in rows], "pagination": build_pagination_meta(total, page, page_size)}
