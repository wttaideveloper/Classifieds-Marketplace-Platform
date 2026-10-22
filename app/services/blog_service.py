"""Blogs and news: platform posts, merchant posts with approval, and categories.

Statuses: draft -> pending_approval -> published, or changes_requested / rejected (the merchant edits and sends it again);
published -> unpublished; anything -> deleted (kept for the record, hidden everywhere).

The public reads only posts that are `is_live`, always from the post's own columns. A merchant's edit of a live post is
kept in `pending_revision` and the post goes back to pending_approval, but it stays live with the old version until a
Super Admin approves the edit. Every status change and edit is written to the post's history with who did it and why.
"""
import re
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.blog_access import BlogActor
from app.core.config import settings
from app.models.blog_model import BlogCategory, BlogPost, BlogPostHistory, slugify
from app.repository import blog_repo
from app.repository.query_utils import build_pagination_meta
from app.schemas.blog_schema import (
    BlogCategoryListResponse, BlogCategoryResponse, BlogDetailResponse, BlogHistoryResponse, BlogListItemResponse,
    BlogManageDetail, BlogManageItem, BlogManagePaginatedResponse, BlogPaginatedResponse,
)
from app.services import blog_notifications
from app.utils.html_sanitize import clean_html

STATUS_LABELS = {
    "draft": "Draft", "pending_approval": "Pending Approval", "changes_requested": "Changes Requested", "rejected": "Rejected",
    "published": "Published", "unpublished": "Unpublished", "deleted": "Deleted",
}
CONTENT_KEYS = ("kind", "title", "excerpt", "content", "cover_image_url", "category_id", "tags")
MERCHANT_EDITABLE = ("draft", "changes_requested", "rejected", "unpublished", "published")
MERCHANT_SUBMITTABLE = ("draft", "changes_requested", "rejected", "unpublished")
ADMIN_PUBLISHABLE = ("draft", "unpublished", "changes_requested", "rejected")


def _now() -> datetime:
    return datetime.utcnow()


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blog post not found")


def _reason(value: str | None, *, required: bool, what: str) -> str | None:
    text = " ".join((value or "").split())
    if required and len(text) < 3:
        raise _unprocessable(f"A reason is required to {what} (3 to 500 characters)")
    return text[:500] or None


def _plain(value: str | None) -> str:
    return re.sub(r"<[^>]*>", "", value or "").strip()


# ---------------------------------------------------------------- the content of a post

def _main_content(post: BlogPost) -> dict:
    return {key: (list(post.tags or []) if key == "tags" else getattr(post, key)) for key in CONTENT_KEYS}


def _working(post: BlogPost) -> dict:
    """The latest version: the merchant's pending edit laid over the live columns."""
    content = _main_content(post)
    for key, value in (post.pending_revision or {}).items():
        if key in CONTENT_KEYS:
            content[key] = UUID(value) if key == "category_id" and value else value
    return content


def _category_name(db: Session, post: BlogPost, category_id: UUID | None) -> str | None:
    if category_id is None:
        return None
    if category_id == post.category_id:
        return post.category
    row = blog_repo.get_category(db, category_id)
    return row.name if row else None


def _set_main(db: Session, post: BlogPost, content: dict) -> None:
    for key in CONTENT_KEYS:
        if key in content:
            setattr(post, key, content[key])
    row = blog_repo.get_category(db, post.category_id) if post.category_id else None
    post.category = row.name if row else None


def _resolve_category(db: Session, category_id: UUID | None, name: str | None, keep: UUID | None) -> BlogCategory:
    row = blog_repo.get_category(db, category_id) if category_id else blog_repo.get_category_by_name(db, name or "")
    if row is None:
        raise _unprocessable("Category not found. Pick one from GET /cms/blogs/categories")
    if not row.is_active and row.id != keep:
        raise _unprocessable(f'The category "{row.name}" is switched off')
    return row


def _incoming(db: Session, payload, current_category: UUID | None) -> dict:
    """The fields the caller sent, cleaned. Only the content fields; nothing the caller did not send."""
    data = payload.model_dump(exclude_unset=True)
    out: dict = {}
    if data.get("kind"):
        out["kind"] = data["kind"]
    if data.get("title") is not None:
        title = " ".join(data["title"].split())
        if not title:
            raise _unprocessable("The title cannot be empty")
        out["title"] = title
    if "excerpt" in data:
        out["excerpt"] = _plain(data["excerpt"]) or None
    if data.get("content") is not None:
        out["content"] = clean_html(data["content"])
    if "cover_image_url" in data:
        out["cover_image_url"] = data["cover_image_url"]
    if data.get("tags") is not None:
        out["tags"] = data["tags"]
    if data.get("category_id") is not None or data.get("category"):
        out["category_id"] = _resolve_category(db, data.get("category_id"), data.get("category"), current_category).id
    elif "category_id" in data and data["category_id"] is None and "category" not in data:
        out["category_id"] = None      # an explicit null takes the category off (a draft can be saved without one)
    return out


def _assert_ready(content: dict, what: str) -> None:
    missing = []
    if not (content.get("title") or "").strip():
        missing.append("title")
    if not content.get("kind"):
        missing.append("kind")
    if not content.get("category_id"):
        missing.append("category")
    body = content.get("content") or ""
    if not _plain(body) and "<img" not in body:
        missing.append("body")
    if missing:
        raise _unprocessable(f"Add the {', '.join(missing)} before you {what}")


def _unique_slug(db: Session, base: str, exclude_id: UUID | None = None) -> str:
    stem = slugify(base)[:240]
    candidate, suffix = stem, 2
    while True:
        existing = blog_repo.get_post_by_slug(db, candidate, include_deleted=True)
        if not existing or (exclude_id and existing.id == exclude_id):
            return candidate
        candidate = f"{stem}-{suffix}"
        suffix += 1


def _json_content(content: dict) -> dict:
    return {k: (str(v) if k == "category_id" and v else v) for k, v in content.items()}


# ---------------------------------------------------------------- history and responses

def _log(db: Session, post: BlogPost, action: str, actor: BlogActor | None, *, from_status: str | None = None,
         to_status: str | None = None, reason: str | None = None) -> None:
    blog_repo.add_history(
        db, post, action=action, from_status=from_status, to_status=to_status or post.status, reason=reason,
        actor_id=actor.id if actor else None, actor_name=actor.name if actor else None, actor_role=actor.role if actor else None,
    )


def _public_dict(post: BlogPost) -> dict:
    return {
        "id": post.id, "title": post.title, "slug": post.slug, "kind": post.kind, "excerpt": post.excerpt,
        "cover_image_url": post.cover_image_url, "author_name": post.author_name, "author_type": post.source,
        "business_name": post.business_name, "business_logo_url": post.business_logo_url, "tenant_id": post.tenant_id,
        "enterprise_id": post.enterprise_id, "category": post.category, "category_id": post.category_id,
        "tags": post.tags or [], "is_pinned": bool(post.is_pinned), "status": "published",
        "published_at": post.published_at, "view_count": post.view_count or 0, "created_at": post.created_at, "updated_at": post.updated_at,
    }


def _actions(post: BlogPost, actor: BlogActor) -> list[str]:
    s = post.status
    if s == "deleted":
        return []
    out: list[str] = []
    if actor.role == "merchant":
        if s in MERCHANT_EDITABLE:
            out.append("edit")
        if s in MERCHANT_SUBMITTABLE:
            out.append("submit")
        if s in ("changes_requested", "rejected") and post.pending_revision:
            out.append("discard_changes")
        if post.is_live:
            out.append("unpublish")
        if s == "draft":
            out.append("delete")
        return out
    if not post.pending_revision:
        out.append("edit")
    if s in ADMIN_PUBLISHABLE:
        out.append("publish")
    if s == "pending_approval" and not (post.source == "merchant" and post.author_id and str(post.author_id) == actor.id):
        out.append("review")
    if post.is_live:
        out.append("unpublish")
    out.append("delete")
    out.append("unpin" if post.is_pinned else "pin")
    return out


def _manage_dict(db: Session, post: BlogPost, actor: BlogActor, *, detail: bool) -> dict:
    working = _working(post)
    base = _public_dict(post)
    base.update({k: working[k] for k in ("kind", "title", "excerpt", "cover_image_url", "category_id")})
    base["tags"] = working["tags"] or []
    base["category"] = _category_name(db, post, working["category_id"])
    base.update({
        "status": post.status, "status_label": STATUS_LABELS.get(post.status, post.status), "is_live": bool(post.is_live),
        "has_pending_revision": bool(post.pending_revision), "reviewer_note": post.reviewer_note, "reviewed_at": post.reviewed_at,
        "submitted_at": post.submitted_at, "actions": _actions(post, actor),
    })
    if detail:
        base.update({"content": working["content"] or "", "author_id": post.author_id, "created_by": post.created_by, "updated_by": post.updated_by})
        main = _main_content(post)
        base["live_version"] = (
            {**main, "category": post.category, "content": main["content"] or ""} if post.pending_revision and post.is_live else None
        )
    return base


def _manage_detail(db: Session, post: BlogPost, actor: BlogActor) -> BlogManageDetail:
    return BlogManageDetail.model_validate(_manage_dict(db, post, actor, detail=True))


def _manage_page(db: Session, query, actor: BlogActor, page: int, page_size: int) -> BlogManagePaginatedResponse:
    total = query.count()
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    return BlogManagePaginatedResponse(
        items=[BlogManageItem.model_validate(_manage_dict(db, p, actor, detail=False)) for p in rows],
        pagination=build_pagination_meta(total, page, page_size),
    )


def _history_for(db: Session, post: BlogPost) -> BlogHistoryResponse:
    rows = db.query(BlogPostHistory).filter(BlogPostHistory.post_id == post.id).order_by(BlogPostHistory.created_at.desc(), BlogPostHistory.id).all()
    return BlogHistoryResponse(items=rows, total=len(rows))


def _statuses_filter(query, status_filter: str | None):
    """Never lists deleted posts, unless they are asked for."""
    if status_filter:
        if status_filter == "archived":
            status_filter = "unpublished"
        if status_filter not in STATUS_LABELS:
            raise _unprocessable(f"status must be one of: {', '.join(STATUS_LABELS)}")
        return query.filter(BlogPost.status == status_filter)
    return query.filter(BlogPost.status != "deleted")


# ---------------------------------------------------------------- public

def list_blogs_service(
    db: Session, *, search: str | None = None, status_filter: str | None = None, category: str | None = None,
    category_id: UUID | None = None, kind: str | None = None, tag: str | None = None, tenant_id: UUID | None = None,
    enterprise_id: UUID | None = None, source: str | None = None, page: int = 1, page_size: int = 20, public_only: bool = True,
) -> BlogPaginatedResponse:
    """Live posts, pinned first and then newest first."""
    query = blog_repo.filter_posts(
        blog_repo.public_posts(db), search=search, kind=kind, category=category, category_id=category_id, tag=tag,
        tenant_id=tenant_id, enterprise_id=enterprise_id, source=source,
    ).order_by(BlogPost.is_pinned.desc(), BlogPost.published_at.desc().nullslast(), BlogPost.created_at.desc())
    total = query.count()
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    return BlogPaginatedResponse(
        items=[BlogListItemResponse.model_validate(_public_dict(p)) for p in rows],
        pagination=build_pagination_meta(total, page, page_size),
    )


def get_blog_service(
    db: Session, *, blog_id: UUID | None = None, slug: str | None = None, public_only: bool = True, increment_views: bool = False,
) -> BlogDetailResponse:
    """A live post, by slug (or by id). A post that is not live is a 404 for the public."""
    post = blog_repo.get_post(db, blog_id) if blog_id else blog_repo.get_post_by_slug(db, slug or "")
    if post is None and slug:
        try:
            post = blog_repo.get_post(db, UUID(slug))
        except ValueError:
            post = None
    if post is None or not post.is_live:
        raise _not_found()
    if increment_views:
        db.query(BlogPost).filter(BlogPost.id == post.id).update({BlogPost.view_count: BlogPost.view_count + 1}, synchronize_session=False)
        db.commit()
        db.refresh(post)
    return BlogDetailResponse.model_validate({**_public_dict(post), "content": post.content, "author_id": None,
                                              "created_by": None, "updated_by": None})


# ---------------------------------------------------------------- merchant

def _business_for(db: Session, actor: BlogActor, enterprise_id: UUID | None):
    from app.models.enterprise_model import Enterprise

    query = db.query(Enterprise).filter(Enterprise.tenant_id == actor.tenant_id, Enterprise.is_deleted.is_(False))
    if enterprise_id is not None:
        row = query.filter(Enterprise.id == enterprise_id).first()
        if row is None:
            raise HTTPException(status_code=403, detail="That business does not belong to your account")
        return row
    row = query.order_by(Enterprise.created_at).first()
    if row is None:
        raise _unprocessable("Your account has no business profile yet. Create the business before writing posts")
    return row


def _stamp_business(post: BlogPost, enterprise) -> None:
    post.enterprise_id = enterprise.id
    post.business_name = enterprise.business_short_name or enterprise.business_legal_name
    post.business_logo_url = enterprise.logo_url


def _my_post(db: Session, actor: BlogActor, post_id: UUID, *, lock: bool = False) -> BlogPost:
    post = blog_repo.get_post(db, post_id, lock=lock)
    if post is None or post.source != "merchant" or post.tenant_id != actor.tenant_id:
        raise _not_found()      # another business's post looks exactly like a post that does not exist
    return post


def create_my_post_service(db: Session, actor: BlogActor, payload) -> BlogManageDetail:
    enterprise = _business_for(db, actor, payload.enterprise_id)
    fields = _incoming(db, payload, None)
    post = BlogPost(
        tenant_id=actor.tenant_id, source="merchant", status="draft", is_live=False, author_id=UUID(actor.id),
        author_name=actor.name, created_by=actor.id, updated_by=actor.id, kind=fields.pop("kind", "blog"),
        slug=_unique_slug(db, fields["title"]), content="", tags=[],
    )
    for key, value in fields.items():
        setattr(post, key, value)
    _stamp_business(post, enterprise)
    _set_main(db, post, {})
    db.add(post)
    db.flush()
    _log(db, post, "created", actor, to_status="draft")
    db.commit()
    db.refresh(post)
    return _manage_detail(db, post, actor)


def list_my_posts_service(
    db: Session, actor: BlogActor, *, status_filter: str | None = None, kind: str | None = None, category_id: UUID | None = None,
    search: str | None = None, page: int = 1, page_size: int = 20,
) -> BlogManagePaginatedResponse:
    query = blog_repo.filter_posts(
        db.query(BlogPost).filter(BlogPost.tenant_id == actor.tenant_id, BlogPost.source == "merchant", BlogPost.is_deleted.is_(False)),
        search=search, kind=kind, category_id=category_id,
    )
    query = _statuses_filter(query, status_filter).order_by(BlogPost.updated_at.desc(), BlogPost.created_at.desc())
    return _manage_page(db, query, actor, page, page_size)


def get_my_post_service(db: Session, actor: BlogActor, post_id: UUID) -> BlogManageDetail:
    return _manage_detail(db, _my_post(db, actor, post_id), actor)


def my_post_history_service(db: Session, actor: BlogActor, post_id: UUID) -> BlogHistoryResponse:
    return _history_for(db, _my_post(db, actor, post_id))


def update_my_post_service(db: Session, actor: BlogActor, post_id: UUID, payload) -> BlogManageDetail:
    post = _my_post(db, actor, post_id, lock=True)
    if post.status == "pending_approval":
        raise _conflict("This post is waiting for review. You can edit it again once it has been decided")
    if post.status not in MERCHANT_EDITABLE:
        raise _conflict(f"A post that is {STATUS_LABELS[post.status].lower()} cannot be edited")
    current = _working(post)
    changes = _incoming(db, payload, current.get("category_id"))
    if payload.enterprise_id is not None and not post.is_live:
        _stamp_business(post, _business_for(db, actor, payload.enterprise_id))
    if not changes:
        db.commit()
        return _manage_detail(db, post, actor)
    previous = post.status
    if post.is_live:
        # the live version stays up; the edit waits in pending_revision
        merged = {**current, **changes}
        _assert_ready(merged, "send this edit for approval")
        post.pending_revision = _json_content(merged)
        if post.status == "published":
            post.status, post.submitted_at, post.reviewer_note = "pending_approval", _now(), None
            post.updated_by = actor.id
            _log(db, post, "edited", actor, from_status=previous)
            _log(db, post, "submitted", actor, from_status=previous, to_status="pending_approval")
            db.commit()
            blog_notifications.notify_submitted(post.id, access_token=actor.token, is_edit=True)
            db.refresh(post)
            return _manage_detail(db, post, actor)
    else:
        _set_main(db, post, changes)
    post.updated_by = actor.id
    _log(db, post, "edited", actor, from_status=previous, to_status=post.status)
    db.commit()
    db.refresh(post)
    return _manage_detail(db, post, actor)


def submit_my_post_service(db: Session, actor: BlogActor, post_id: UUID) -> BlogManageDetail:
    post = _my_post(db, actor, post_id, lock=True)
    if post.status not in MERCHANT_SUBMITTABLE:
        raise _conflict(f"A post that is {STATUS_LABELS[post.status].lower()} cannot be sent for approval")
    _assert_ready(_working(post), "send this for approval")
    previous = post.status
    post.status, post.submitted_at, post.reviewer_note, post.updated_by = "pending_approval", _now(), None, actor.id
    _log(db, post, "submitted", actor, from_status=previous, to_status="pending_approval")
    db.commit()
    blog_notifications.notify_submitted(post.id, access_token=actor.token, is_edit=bool(post.is_live))
    db.refresh(post)
    return _manage_detail(db, post, actor)


def discard_my_changes_service(db: Session, actor: BlogActor, post_id: UUID) -> BlogManageDetail:
    """Drop an edit of a live post that was sent back, so the post is just published again."""
    post = _my_post(db, actor, post_id, lock=True)
    if not (post.pending_revision and post.status in ("changes_requested", "rejected")):
        raise _conflict("There are no sent-back changes to discard")
    previous = post.status
    post.pending_revision, post.status, post.reviewer_note, post.updated_by = None, "published", None, actor.id
    _log(db, post, "edited", actor, from_status=previous, to_status="published", reason="Discarded the changes")
    db.commit()
    db.refresh(post)
    return _manage_detail(db, post, actor)


def _take_down(db: Session, post: BlogPost, actor: BlogActor, reason: str | None) -> None:
    """Unpublish: the edit that was waiting becomes the post's own content, and the public no longer sees it."""
    previous = post.status
    if post.pending_revision:
        _set_main(db, post, _working(post))
        post.pending_revision = None
    post.status, post.is_live, post.is_pinned, post.updated_by = "unpublished", False, False, actor.id
    post.reviewer_note = reason if actor.role == "super_admin" else None
    if actor.role == "super_admin":
        post.reviewed_by, post.reviewed_at = actor.id, _now()
    _log(db, post, "unpublished", actor, from_status=previous, to_status="unpublished", reason=reason)


def unpublish_my_post_service(db: Session, actor: BlogActor, post_id: UUID, reason: str | None) -> BlogManageDetail:
    post = _my_post(db, actor, post_id, lock=True)
    if not post.is_live:
        raise _conflict("This post is not live")
    _take_down(db, post, actor, _reason(reason, required=False, what="unpublish"))
    db.commit()
    db.refresh(post)
    return _manage_detail(db, post, actor)


def delete_my_post_service(db: Session, actor: BlogActor, post_id: UUID) -> None:
    post = _my_post(db, actor, post_id, lock=True)
    if post.status != "draft":
        raise _conflict("Only a draft can be deleted. Unpublish a live post instead")
    _remove(db, post, actor, None)
    db.commit()


def _remove(db: Session, post: BlogPost, actor: BlogActor, reason: str | None) -> None:
    previous = post.status
    post.status, post.is_deleted, post.deleted_at, post.is_live = "deleted", True, _now(), False
    post.is_pinned, post.pending_revision, post.updated_by = False, None, actor.id
    if actor.role == "super_admin":
        post.reviewer_note, post.reviewed_by, post.reviewed_at = reason, actor.id, post.deleted_at
    _log(db, post, "deleted", actor, from_status=previous, to_status="deleted", reason=reason)


# ---------------------------------------------------------------- Super Admin

def _admin_post(db: Session, post_id: UUID, *, lock: bool = False) -> BlogPost:
    post = blog_repo.get_post(db, post_id, lock=lock)
    if post is None:
        raise _not_found()
    return post


def create_blog_service(db: Session, payload, actor: BlogActor) -> BlogManageDetail:
    fields = _incoming(db, payload, None)
    post = BlogPost(
        tenant_id=payload.tenant_id, enterprise_id=payload.enterprise_id, source="platform", status="draft", is_live=False,
        author_id=UUID(actor.id), author_name=payload.author_name or actor.name or getattr(settings, "PLATFORM_NAME", None) or "Platform",
        created_by=actor.id, updated_by=actor.id, kind=fields.pop("kind", "blog"),
        slug=_unique_slug(db, payload.slug or fields["title"]), content="", tags=[],
    )
    for key, value in fields.items():
        setattr(post, key, value)
    _set_main(db, post, {})
    db.add(post)
    db.flush()
    _log(db, post, "created", actor, to_status="draft")
    if payload.status == "published":
        _assert_ready(_main_content(post), "publish this")
        _go_live(post, actor)
        _log(db, post, "published", actor, from_status="draft", to_status="published")
    db.commit()
    db.refresh(post)
    return _manage_detail(db, post, actor)


def _go_live(post: BlogPost, actor: BlogActor) -> None:
    was_live = post.is_live
    post.status, post.is_live, post.updated_by = "published", True, actor.id
    if not was_live or post.published_at is None:
        post.published_at = _now()
    post.reviewer_note, post.submitted_at = None, None
    post.reviewed_by, post.reviewed_at = actor.id, _now()


def list_admin_posts_service(
    db: Session, actor: BlogActor, *, status_filter: str | None = None, source: str | None = None, kind: str | None = None,
    category_id: UUID | None = None, category: str | None = None, tag: str | None = None, search: str | None = None,
    tenant_id: UUID | None = None, enterprise_id: UUID | None = None, pinned: bool | None = None, page: int = 1, page_size: int = 20,
) -> BlogManagePaginatedResponse:
    query = blog_repo.filter_posts(
        db.query(BlogPost), search=search, kind=kind, category=category, category_id=category_id, tag=tag, tenant_id=tenant_id,
        enterprise_id=enterprise_id, source=source,
    )
    if pinned is not None:
        query = query.filter(BlogPost.is_pinned.is_(pinned))
    query = _statuses_filter(query, status_filter).order_by(BlogPost.created_at.desc())
    return _manage_page(db, query, actor, page, page_size)


def approval_queue_service(
    db: Session, actor: BlogActor, *, kind: str | None = None, category_id: UUID | None = None, tenant_id: UUID | None = None,
    enterprise_id: UUID | None = None, search: str | None = None, page: int = 1, page_size: int = 20,
) -> BlogManagePaginatedResponse:
    """Posts waiting for a decision, oldest first."""
    query = blog_repo.filter_posts(
        db.query(BlogPost).filter(BlogPost.status == "pending_approval", BlogPost.is_deleted.is_(False)),
        search=search, kind=kind, category_id=category_id, tenant_id=tenant_id, enterprise_id=enterprise_id,
    ).order_by(BlogPost.submitted_at.asc().nullslast(), BlogPost.created_at.asc())
    return _manage_page(db, query, actor, page, page_size)


def get_admin_post_service(db: Session, actor: BlogActor, post_id: UUID) -> BlogManageDetail:
    return _manage_detail(db, _admin_post(db, post_id), actor)


def admin_post_history_service(db: Session, post_id: UUID) -> BlogHistoryResponse:
    return _history_for(db, _admin_post(db, post_id))


def update_blog_service(db: Session, post_id: UUID, payload, actor: BlogActor) -> BlogManageDetail:
    """A Super Admin's edit applies at once, to a live post too. If the merchant's edit is waiting, decide that first."""
    post = _admin_post(db, post_id, lock=True)
    if post.pending_revision:
        raise _conflict("The merchant has an edit waiting for approval. Approve or reject it before editing this post")
    changes = _incoming(db, payload, post.category_id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("slug"):
        post.slug = _unique_slug(db, data["slug"], exclude_id=post.id)
    if data.get("author_name") and post.source == "platform":
        post.author_name = data["author_name"].strip()
    if changes:
        _set_main(db, post, changes)
        if post.is_live:
            _assert_ready(_main_content(post), "save this live post")
    post.updated_by = actor.id
    _log(db, post, "edited", actor, to_status=post.status)
    db.commit()
    db.refresh(post)
    return _manage_detail(db, post, actor)


def publish_blog_service(db: Session, post_id: UUID, actor: BlogActor) -> BlogManageDetail:
    post = _admin_post(db, post_id, lock=True)
    if post.status == "pending_approval":
        raise _conflict("This post is waiting for review. Approve it from the approval queue")
    if post.status not in ADMIN_PUBLISHABLE:
        raise _conflict(f"A post that is {STATUS_LABELS[post.status].lower()} cannot be published")
    _assert_ready(_main_content(post), "publish this")
    previous = post.status
    _go_live(post, actor)
    _log(db, post, "published", actor, from_status=previous, to_status="published")
    db.commit()
    blog_notifications.notify_decision(post.id, "approved")
    db.refresh(post)
    return _manage_detail(db, post, actor)


def _needs_reason(post: BlogPost) -> bool:
    return post.source == "merchant"


def unpublish_blog_service(db: Session, post_id: UUID, actor: BlogActor, reason: str | None) -> BlogManageDetail:
    post = _admin_post(db, post_id, lock=True)
    if not post.is_live:
        raise _conflict("This post is not live")
    why = _reason(reason, required=_needs_reason(post), what="take down a merchant's post")
    _take_down(db, post, actor, why)
    db.commit()
    if post.source == "merchant":
        blog_notifications.notify_decision(post.id, "unpublished", reason=why)
    db.refresh(post)
    return _manage_detail(db, post, actor)


def delete_blog_service(db: Session, post_id: UUID, actor: BlogActor, reason: str | None) -> None:
    post = _admin_post(db, post_id, lock=True)
    why = _reason(reason, required=_needs_reason(post), what="delete a merchant's post")
    _remove(db, post, actor, why)
    db.commit()
    if post.source == "merchant":
        blog_notifications.notify_decision(post.id, "deleted", reason=why)


def update_blog_status_service(db: Session, post_id: UUID, new_status: str, actor: BlogActor, reason: str | None = None) -> BlogManageDetail:
    """The older single-endpoint way to publish / unpublish. draft takes a live post down and keeps it as a draft."""
    if new_status == "published":
        return publish_blog_service(db, post_id, actor)
    if new_status in ("unpublished", "archived"):
        return unpublish_blog_service(db, post_id, actor, reason)
    post = _admin_post(db, post_id, lock=True)
    if post.status == "draft":
        return _manage_detail(db, post, actor)
    if post.status == "pending_approval":
        raise _conflict("This post is waiting for review")
    if post.is_live:
        why = _reason(reason, required=_needs_reason(post), what="take down a merchant's post")
        _take_down(db, post, actor, why)
    previous = post.status
    post.status = "draft"
    _log(db, post, "edited", actor, from_status=previous, to_status="draft", reason="Moved back to draft")
    db.commit()
    db.refresh(post)
    return _manage_detail(db, post, actor)


def review_blog_service(db: Session, post_id: UUID, actor: BlogActor, action: str, reason: str | None) -> BlogManageDetail:
    post = _admin_post(db, post_id, lock=True)
    if post.status != "pending_approval":
        raise _conflict(f"Only a post that is pending approval can be reviewed. This one is {STATUS_LABELS[post.status].lower()}")
    if post.source == "merchant" and post.author_id is not None and str(post.author_id) == actor.id:
        raise HTTPException(status_code=403, detail="You cannot review your own post")
    why = _reason(reason, required=action != "approve", what="request changes" if action == "request_changes" else "reject a post")
    if action == "approve":
        if post.pending_revision:
            _set_main(db, post, _working(post))
            post.pending_revision = None
        _assert_ready(_main_content(post), "approve this")
        _go_live(post, actor)
        post.reviewer_note = why
        _log(db, post, "approved", actor, from_status="pending_approval", to_status="published", reason=why)
        outcome = "approved"
    else:
        outcome = "changes_requested" if action == "request_changes" else "rejected"
        post.status, post.reviewer_note, post.reviewed_by, post.reviewed_at, post.updated_by = outcome, why, actor.id, _now(), actor.id
        _log(db, post, outcome, actor, from_status="pending_approval", to_status=outcome, reason=why)
    db.commit()
    blog_notifications.notify_decision(post.id, outcome, reason=why if outcome != "approved" else None)
    db.refresh(post)
    return _manage_detail(db, post, actor)


def pin_blog_service(db: Session, post_id: UUID, actor: BlogActor, pinned: bool) -> BlogManageDetail:
    post = _admin_post(db, post_id, lock=True)
    if bool(post.is_pinned) != pinned:
        post.is_pinned, post.updated_by = pinned, actor.id
        _log(db, post, "pinned" if pinned else "unpinned", actor, to_status=post.status)
    db.commit()
    db.refresh(post)
    return _manage_detail(db, post, actor)


# ---------------------------------------------------------------- categories

def _category_dict(row: BlogCategory, count: int | None) -> BlogCategoryResponse:
    return BlogCategoryResponse(id=row.id, name=row.name, is_active=row.is_active, sort_order=row.sort_order, post_count=count)


def list_categories_service(db: Session, *, public: bool, kind: str | None = None) -> BlogCategoryListResponse:
    """The public list has only active categories, counting live posts. The admin list has all, counting every post still kept."""
    query = db.query(BlogCategory)
    counts = db.query(BlogPost.category_id, func.count(BlogPost.id)).filter(BlogPost.category_id.isnot(None), BlogPost.is_deleted.is_(False))
    if public:
        query = query.filter(BlogCategory.is_active.is_(True))
        counts = counts.filter(BlogPost.is_live.is_(True))
    if kind:
        counts = counts.filter(BlogPost.kind == kind)
    by_category = dict(counts.group_by(BlogPost.category_id).all())
    rows = query.order_by(BlogCategory.sort_order, func.lower(BlogCategory.name)).all()
    items = [_category_dict(r, int(by_category.get(r.id, 0))) for r in rows]
    return BlogCategoryListResponse(items=items, total=len(items))


def _clean_name(name: str) -> str:
    text = " ".join(name.split())
    if not text:
        raise _unprocessable("The category name cannot be empty")
    return text


def create_category_service(db: Session, payload) -> BlogCategoryResponse:
    name = _clean_name(payload.name)
    if blog_repo.get_category_by_name(db, name):
        raise _conflict(f'A category named "{name}" already exists')
    top = db.query(func.max(BlogCategory.sort_order)).scalar()
    row = BlogCategory(name=name, name_key=name.lower(), is_active=payload.is_active, sort_order=(top + 1) if top is not None else 0)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _category_dict(row, 0)


def update_category_service(db: Session, category_id: UUID, payload) -> BlogCategoryResponse:
    row = blog_repo.get_category(db, category_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Category not found")
    if payload.name is not None:
        name = _clean_name(payload.name)
        clash = blog_repo.get_category_by_name(db, name)
        if clash is not None and clash.id != row.id:
            raise _conflict(f'A category named "{name}" already exists')
        row.name, row.name_key = name, name.lower()
        db.query(BlogPost).filter(BlogPost.category_id == row.id).update({BlogPost.category: name}, synchronize_session=False)
    if payload.is_active is not None:
        row.is_active = payload.is_active
    db.commit()
    db.refresh(row)
    count = db.query(func.count(BlogPost.id)).filter(BlogPost.category_id == row.id, BlogPost.is_deleted.is_(False)).scalar()
    return _category_dict(row, int(count or 0))


def reorder_categories_service(db: Session, ids: list[UUID]) -> BlogCategoryListResponse:
    rows = {r.id: r for r in db.query(BlogCategory).all()}
    if len(set(ids)) != len(ids) or set(ids) != set(rows):
        raise _unprocessable("Send every category id exactly once, in the order they should appear")
    for position, category_id in enumerate(ids):
        rows[category_id].sort_order = position
    db.commit()
    return list_categories_service(db, public=False)


def delete_category_service(db: Session, category_id: UUID) -> None:
    row = blog_repo.get_category(db, category_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Category not found")
    if db.query(func.count(BlogPost.id)).filter(BlogPost.category_id == row.id).scalar():
        raise _conflict("This category has posts. Switch it off instead of deleting it")
    db.delete(row)
    db.commit()
