"""Blogs and news: platform posts, merchant posts with approval, and categories.

Statuses: draft -> pending_approval -> published, or changes_requested / rejected (the merchant edits and sends it again);
published -> unpublished; anything -> deleted (kept for the record, hidden everywhere).

The public reads only posts that are `is_live`, always from the post's own columns. A merchant's edit of a live post is
kept in `pending_revision` and the post goes back to pending_approval, but it stays live with the old version until a
Super Admin approves the edit. Every status change and edit is written to the post's history with who did it and why.

A post is written with a form (see blog_form_service). A live post, and a post waiting for approval, keep the form version they
have; any other post follows the active form each time it is saved. Every change raises the post's `version`, and a caller may
send the version it loaded so that a change made meanwhile is refused (409 VERSION_CONFLICT) instead of being overwritten.
"""
from datetime import datetime
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.blog_access import BlogActor
from app.core.config import settings
from app.exceptions.problem import ProblemHTTPException, problem
from app.models.blog_model import BlogCategory, BlogPost, BlogPostHistory, slugify
from app.repository import blog_repo
from app.repository.query_utils import build_pagination_meta
from app.schemas.blog_schema import (
    BlogCategoryListResponse, BlogCategoryResponse, BlogDetailResponse, BlogHistoryResponse, BlogListItemResponse,
    BlogManageDetail, BlogManageItem, BlogManagePaginatedResponse, BlogPaginatedResponse, BlogPreviewResponse, BlogRevisionDetail,
    BlogRevisionList,
)
from app.services import blog_form_rules as rules
from app.services import blog_form_service as forms
from app.services import blog_governance_service as governance
from app.services import blog_notifications, blog_upload_service as uploads
from app.utils.html_sanitize import clean_html

STATUS_LABELS = {
    "draft": "Draft", "pending_approval": "Pending Approval", "changes_requested": "Changes Requested", "rejected": "Rejected",
    "published": "Published", "unpublished": "Unpublished", "deleted": "Deleted",
}
LIST_KEYS = ("tags", "additional_images", "reference_urls", "supporting_documents")
CONTENT_KEYS = ("kind", "title", "excerpt", "content", "cover_image_url", "category_id", "tags", "additional_images", "reference_urls",
                "supporting_documents", "custom_values")
# the form's core field -> the post's attribute
CORE_ATTR = {"title": "title", "excerpt": "excerpt", "content": "content", "category": "category_id", "tags": "tags", "cover_image": "cover_image_url",
             "additional_images": "additional_images", "reference_urls": "reference_urls", "supporting_documents": "supporting_documents"}
SNAPSHOT_ACTIONS = ("created", "edited", "submitted", "approved", "published")
MERCHANT_EDITABLE = ("draft", "changes_requested", "rejected", "unpublished", "published")
MERCHANT_SUBMITTABLE = ("draft", "changes_requested", "rejected", "unpublished")
ADMIN_PUBLISHABLE = ("draft", "unpublished", "changes_requested", "rejected")
RESERVED_SLUGS = ("categories", "my", "admin", "files", "uploads", "forms")


def _now() -> datetime:
    return datetime.utcnow()


def _conflict(detail: str, code: str = "INVALID_STATUS", **extra) -> ProblemHTTPException:
    return problem(409, detail, code, **extra)


def _unprocessable(detail: str, code: str = "VALIDATION_FAILED", field: str | None = None) -> ProblemHTTPException:
    errors = [rules.err(field or "", code, detail)] if field else []
    return problem(422, detail, code, errors=errors)


def _not_found() -> ProblemHTTPException:
    return problem(404, "Blog post not found", "POST_NOT_FOUND")


def _expect(post: BlogPost, expected: int | None) -> None:
    """Refuse a change made on an out-of-date copy of the post."""
    if expected is not None and expected != (post.version or 1):
        raise _conflict("This post was changed after you opened it. Reload it, then apply your changes again", "VERSION_CONFLICT",
                        current_version=post.version or 1)


def _reason(value: str | None, *, required: bool, what: str) -> str | None:
    text = " ".join((value or "").split())
    if required and len(text) < 3:
        raise _unprocessable(f"A reason is required to {what} (3 to 500 characters)", "REASON_REQUIRED", "reason")
    return text[:500] or None


def _plain(value: str | None) -> str:
    return rules.plain(value)


# ---------------------------------------------------------------- the content of a post

def _main_content(post: BlogPost) -> dict:
    out = {}
    for key in CONTENT_KEYS:
        value = getattr(post, key)
        out[key] = list(value or []) if key in LIST_KEYS else dict(value or {}) if key == "custom_values" else value
    return out


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
        raise _unprocessable("Category not found. Pick one from GET /cms/blogs/categories", "CATEGORY_NOT_FOUND", "category")
    if not row.is_active and row.id != keep:
        raise _unprocessable(f'The category "{row.name}" is switched off', "CATEGORY_INACTIVE", "category")
    return row


def _json_content(content: dict) -> dict:
    return {k: (str(v) if k == "category_id" and v else v) for k, v in content.items()}


def _core_values(content: dict) -> dict:
    return {key: content.get(attr) for key, attr in CORE_ATTR.items()}


def _clean_items(items: list[dict], keys: tuple[str, ...]) -> list[dict]:
    return [{k: v for k, v in item.items() if k in keys and v not in (None, "")} for item in items]


def _link_files(db: Session, actor: BlogActor, out: dict, errors: list) -> None:
    """Uploaded files may only be attached by the business that uploaded them (or a Super Admin), in the right slot."""
    slots = [("cover_image_url", [out["cover_image_url"]] if out.get("cover_image_url") else [], "cover_image", ("cover_image", "image")),
             ("additional_images", [i["url"] for i in out.get("additional_images") or []], "additional_images", ("cover_image", "image")),
             ("supporting_documents", [i["url"] for i in out.get("supporting_documents") or []], "supporting_documents", ("document",))]
    for attr, urls, field, purposes in slots:
        found = uploads.owned_files(db, urls, actor)
        for url in urls:
            if "/cms/blogs/files/" in url and url not in found:
                errors.append(rules.err(field, "FILE_NOT_FOUND", "That file was not uploaded by your business, or it no longer exists"))
            elif url in found and found[url].purpose not in purposes:
                errors.append(rules.err(field, "WRONG_FILE_KIND", f"A {found[url].purpose.replace('_', ' ')} upload cannot be used here"))
        if attr == "supporting_documents":
            for item in out.get("supporting_documents") or []:
                row = found.get(item["url"])
                if row is not None:
                    item.setdefault("name", row.original_name)
                    item.setdefault("size", row.size)
                    item.setdefault("content_type", row.content_type)


def _incoming(db: Session, payload, current_category: UUID | None, actor: BlogActor, errors: list) -> dict:
    """The fields the caller sent, cleaned. Only the content fields; nothing the caller did not send. Problems go to `errors`."""
    data = payload.model_dump(exclude_unset=True)
    out: dict = {}
    if data.get("kind"):
        out["kind"] = data["kind"]
    if data.get("title") is not None:
        title = " ".join(data["title"].split())
        if not title:
            errors.append(rules.err("title", "REQUIRED", "The title cannot be empty"))
        else:
            out["title"] = title
    if "excerpt" in data:
        out["excerpt"] = _plain(data["excerpt"]) or None
    if data.get("content") is not None:
        out["content"] = clean_html(data["content"])
    if "cover_image_url" in data:
        out["cover_image_url"] = data["cover_image_url"]
    if data.get("tags") is not None:
        out["tags"] = data["tags"]
    if data.get("additional_images") is not None:
        out["additional_images"] = _clean_items(data["additional_images"], ("url", "alt", "caption"))
    if data.get("reference_urls") is not None:
        out["reference_urls"] = _clean_items(data["reference_urls"], ("url", "label"))
    if data.get("supporting_documents") is not None:
        out["supporting_documents"] = _clean_items(data["supporting_documents"], ("url", "name", "size", "content_type"))
    try:
        if data.get("category_id") is not None or data.get("category"):
            out["category_id"] = _resolve_category(db, data.get("category_id"), data.get("category"), current_category).id
        elif "category_id" in data and data["category_id"] is None and "category" not in data:
            out["category_id"] = None      # an explicit null takes the category off (a draft can be saved without one)
    except ProblemHTTPException as exc:
        errors.append(rules.err("category", exc.code, exc.detail))
    _link_files(db, actor, out, errors)
    return out


def _form_for(db: Session, post: BlogPost | None, kind: str, tenant_id: UUID | None, source: str) -> forms.ResolvedForm:
    """Live posts and posts waiting for approval keep their form version; other posts follow the active form."""
    if post is not None and (post.is_live or post.status == "pending_approval"):
        return forms.resolve_pinned(db, post.form_id, post.form_version)
    return forms.resolve_active(db, kind, tenant_id if source == "merchant" else None)


def _bind(post: BlogPost, form: forms.ResolvedForm) -> None:
    post.form_id, post.form_version = form.form_id, (form.version or None)


def _check_title(db: Session, title: str, kind: str, *, tenant_id: UUID | None, source: str, exclude_id: UUID | None) -> None:
    if not settings.BLOG_REQUIRE_UNIQUE_TITLES:
        return
    query = db.query(BlogPost.id).filter(func.lower(BlogPost.title) == title.lower(), BlogPost.kind == kind, BlogPost.is_deleted.is_(False))
    query = query.filter(BlogPost.source == "platform") if source == "platform" else query.filter(BlogPost.source == "merchant", BlogPost.tenant_id == tenant_id)
    if exclude_id:
        query = query.filter(BlogPost.id != exclude_id)
    if query.first() is not None:
        raise problem(409, "Another post of the same kind already has this title. Use a different title", "DUPLICATE_TITLE", field="title")


def _prepare(db: Session, actor: BlogActor, post: BlogPost | None, payload, *, tenant_id: UUID | None, source: str) -> tuple[dict, forms.ResolvedForm]:
    """Clean what the caller sent and check it against the form the post is written with. Raises 422 with every problem."""
    errors: list[dict] = []
    current = _working(post) if post is not None else {}
    changes = _incoming(db, payload, current.get("category_id"), actor, errors)
    merged = {**current, **changes}
    kind = merged.get("kind") or "blog"
    form = _form_for(db, post, kind, tenant_id, source)
    enterprise = source == "merchant"
    data = payload.model_dump(exclude_unset=True)
    if "custom_fields" in data:
        custom, custom_errors = rules.clean_custom(form.definition, data["custom_fields"], enterprise_post=enterprise, current=current.get("custom_values"))
        errors += custom_errors
        if not custom_errors:
            changes["custom_values"] = custom
    changed_core = {key: changes[attr] for key, attr in CORE_ATTR.items() if attr in changes}
    errors += rules.check_core(form.definition, changed_core, enterprise_post=enterprise)
    if errors:
        raise rules.invalid(errors)
    if changes.get("title") or (post is None and merged.get("title")):
        _check_title(db, merged["title"], kind, tenant_id=tenant_id, source=source, exclude_id=post.id if post is not None else None)
    elif "kind" in changes and post is not None:
        _check_title(db, merged["title"], kind, tenant_id=tenant_id, source=source, exclude_id=post.id)
    return changes, form


def _ready(db: Session, post: BlogPost, content: dict, what: str) -> forms.ResolvedForm:
    """The post may go to review or live only when the form's required fields are filled. A post that is not live takes the active form now."""
    form = _form_for(db, post, content.get("kind") or post.kind, post.tenant_id, post.source)
    problems = rules.recheck(form.definition, _core_values(content), content.get("custom_values"), enterprise_post=post.source == "merchant")
    if problems:
        names = rules.names_of_missing(form.definition, problems)
        raise rules.invalid(problems, f"Add the {', '.join(names)} before you {what}" if names else None)
    if not (post.is_live or post.status == "pending_approval"):
        _bind(post, form)
    return form


def _explicit_slug(db: Session, raw: str, exclude_id: UUID | None = None) -> str:
    if not any(ch.isalnum() for ch in raw):
        raise _unprocessable("The slug needs letters or digits", "INVALID_SLUG", "slug")
    slug = slugify(raw)[:240]
    if slug in RESERVED_SLUGS:
        raise _unprocessable(f'"{slug}" is reserved. Choose another slug', "RESERVED_SLUG", "slug")
    existing = blog_repo.get_post_by_slug(db, slug, include_deleted=True)
    if existing is not None and existing.id != exclude_id:
        raise problem(409, "Another post already uses this slug", "DUPLICATE_SLUG", field="slug")
    return slug


def _unique_slug(db: Session, base: str, exclude_id: UUID | None = None) -> str:
    stem = slugify(base)[:240]
    candidate, suffix = stem, 2
    while True:
        existing = blog_repo.get_post_by_slug(db, candidate, include_deleted=True)
        if candidate not in RESERVED_SLUGS and (not existing or (exclude_id and existing.id == exclude_id)):
            return candidate
        candidate = f"{stem}-{suffix}"
        suffix += 1


# ---------------------------------------------------------------- history and responses

def _snapshot(post: BlogPost) -> dict:
    content = _json_content(_working(post))
    content["custom_fields"] = content.pop("custom_values", None) or {}
    content["form"] = {"form_id": str(post.form_id) if post.form_id else None, "version": post.form_version}
    return content


def _log(db: Session, post: BlogPost, action: str, actor: BlogActor | None, *, from_status: str | None = None,
         to_status: str | None = None, reason: str | None = None, bump: bool = True) -> None:
    """Writes the history row. Each change raises the post's version once; the row records the version after it."""
    if bump:
        post.version = (post.version or 0) + 1
    blog_repo.add_history(
        db, post, action=action, from_status=from_status, to_status=to_status or post.status, reason=reason,
        actor_id=actor.id if actor else None, actor_name=actor.name if actor else None, actor_role=actor.role if actor else None,
        post_version=post.version, snapshot=_snapshot(post) if action in SNAPSHOT_ACTIONS else None,
    )


def _public_dict(post: BlogPost) -> dict:
    return {
        "id": post.id, "title": post.title, "slug": post.slug, "kind": post.kind, "module": post.module or "general", "excerpt": post.excerpt,
        "cover_image_url": post.cover_image_url, "author_name": post.author_name, "author_type": post.source,
        "business_name": post.business_name, "business_logo_url": post.business_logo_url, "tenant_id": post.tenant_id,
        "enterprise_id": post.enterprise_id, "category": post.category, "category_id": post.category_id,
        "tags": post.tags or [], "is_pinned": bool(post.is_pinned), "status": "published",
        "published_at": post.published_at, "view_count": post.view_count or 0, "created_at": post.created_at, "updated_at": post.updated_at,
    }


def _public_extras(form: forms.ResolvedForm, content: dict) -> dict:
    """What only the detail shows. A list is shown only if the form marks it public; custom fields only the public ones, with labels."""
    by_key = {f["key"]: f for f in rules.fields_of(form.definition)}
    out: dict = {}
    for key in ("additional_images", "reference_urls", "supporting_documents"):
        field = by_key.get(key)
        shown = field is not None and field.get("visible", True) and field.get("visibility") == "public"
        out[key] = list(content.get(key) or []) if shown else []
    out["custom_fields"] = rules.public_custom(form.definition, content.get("custom_values"))
    return out


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


def _pending_info(post: BlogPost) -> dict | None:
    if not post.pending_revision:
        return None
    main, revised = _main_content(post), _working(post)
    changed = ["custom_fields" if k == "custom_values" else k for k in CONTENT_KEYS if revised.get(k) != main.get(k)]
    return {"submitted_at": post.submitted_at, "changed_fields": changed, "status": post.status}


def _form_ref(form: forms.ResolvedForm) -> dict:
    return {"form_id": form.form_id, "version": form.version, "name": form.name, "mode": form.mode, "form_type": form.form_type, "definition": form.definition}


def _view(content: dict) -> dict:
    out = dict(content)
    out["custom_fields"] = out.pop("custom_values", None) or {}
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
        "submitted_at": post.submitted_at, "actions": _actions(post, actor), "version": post.version or 1,
        "form_id": post.form_id, "form_version": post.form_version,
    })
    if detail:
        view = _view(working)
        base.update({"content": working["content"] or "", "author_id": post.author_id, "created_by": post.created_by, "updated_by": post.updated_by,
                     "additional_images": view["additional_images"], "reference_urls": view["reference_urls"],
                     "supporting_documents": view["supporting_documents"], "custom_fields": view["custom_fields"],
                     "pending_revision": _pending_info(post),
                     "form": _form_ref(_form_for(db, post, working["kind"], post.tenant_id, post.source))})
        main = _view(_main_content(post))
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


def _revisions_for(db: Session, post: BlogPost) -> BlogRevisionList:
    rows = db.query(BlogPostHistory).filter(BlogPostHistory.post_id == post.id, BlogPostHistory.snapshot.isnot(None)).order_by(
        BlogPostHistory.created_at.desc(), BlogPostHistory.id).all()
    return BlogRevisionList(items=[_revision_item(r) for r in rows], total=len(rows))


def _revision_item(row: BlogPostHistory) -> dict:
    snap = row.snapshot or {}
    return {"id": row.id, "action": row.action, "post_version": row.post_version, "title": snap.get("title"),
            "form_version": (snap.get("form") or {}).get("version"), "from_status": row.from_status, "to_status": row.to_status, "reason": row.reason,
            "actor_id": row.actor_id, "actor_name": row.actor_name, "actor_role": row.actor_role, "created_at": row.created_at}


def _revision_detail(db: Session, post: BlogPost, history_id: UUID) -> BlogRevisionDetail:
    row = db.query(BlogPostHistory).filter(BlogPostHistory.id == history_id, BlogPostHistory.post_id == post.id).first()
    if row is None or row.snapshot is None:
        raise problem(404, "That revision does not exist", "REVISION_NOT_FOUND")
    return BlogRevisionDetail.model_validate({**_revision_item(row), "snapshot": row.snapshot})


def _statuses_filter(query, status_filter: str | None):
    """Never lists deleted posts, unless they are asked for."""
    if status_filter:
        if status_filter == "archived":
            status_filter = "unpublished"
        if status_filter not in STATUS_LABELS:
            raise _unprocessable(f"status must be one of: {', '.join(STATUS_LABELS)}", "INVALID_STATUS_FILTER", "status")
        return query.filter(BlogPost.status == status_filter)
    return query.filter(BlogPost.status != "deleted")


# ---------------------------------------------------------------- previews

def _preview(db: Session, *, content: dict, form: forms.ResolvedForm, source: str, meta: dict) -> BlogPreviewResponse:
    problems = rules.recheck(form.definition, _core_values(content), content.get("custom_values"), enterprise_post=source == "merchant")
    view = _public_extras(form, content)
    return BlogPreviewResponse.model_validate({
        **meta, "kind": content.get("kind") or "blog", "title": content.get("title") or "", "excerpt": content.get("excerpt"),
        "content": content.get("content") or "", "cover_image_url": content.get("cover_image_url"), "tags": content.get("tags") or [],
        "category_id": content.get("category_id"), **view, "form": _form_ref(form), "ready_to_submit": not problems, "problems": problems,
        "author_type": source,
    })


def preview_post_service(db: Session, actor: BlogActor, post: BlogPost) -> BlogPreviewResponse:
    """The saved working copy (a merchant's waiting edit included) as a reader would see it."""
    working = _working(post)
    form = _form_for(db, post, working["kind"], post.tenant_id, post.source)
    meta = {"id": post.id, "slug": post.slug, "status": post.status, "author_name": post.author_name, "business_name": post.business_name,
            "business_logo_url": post.business_logo_url, "category": _category_name(db, post, working["category_id"])}
    return _preview(db, content=working, form=form, source=post.source, meta=meta)


def preview_payload_service(db: Session, actor: BlogActor, payload) -> BlogPreviewResponse:
    """An unsaved post as a reader would see it. Nothing is stored."""
    source = "merchant" if actor.role == "merchant" else "platform"
    errors: list[dict] = []
    changes = _incoming(db, payload, None, actor, errors)
    content = {"kind": "blog", "title": "", "excerpt": None, "content": "", "cover_image_url": None, "category_id": None, "tags": [],
               "additional_images": [], "reference_urls": [], "supporting_documents": [], "custom_values": {}, **changes}
    form = forms.resolve_active(db, content["kind"], actor.tenant_id if source == "merchant" else None)
    data = payload.model_dump(exclude_unset=True)
    if "custom_fields" in data:
        custom, custom_errors = rules.clean_custom(form.definition, data["custom_fields"], enterprise_post=source == "merchant")
        errors += custom_errors
        content["custom_values"] = custom
    errors += rules.check_core(form.definition, {k: changes[a] for k, a in CORE_ATTR.items() if a in changes}, enterprise_post=source == "merchant")
    if errors:
        raise rules.invalid(errors)
    enterprise = _business_for(db, actor, getattr(payload, "enterprise_id", None)) if source == "merchant" else None
    row = blog_repo.get_category(db, content["category_id"]) if content["category_id"] else None
    meta = {"status": "draft", "author_name": actor.name, "category": row.name if row else None,
            "business_name": (enterprise.business_short_name or enterprise.business_legal_name) if enterprise else None,
            "business_logo_url": enterprise.logo_url if enterprise else None}
    return _preview(db, content=content, form=form, source=source, meta=meta)


# ---------------------------------------------------------------- public

def list_blogs_service(
    db: Session, *, search: str | None = None, status_filter: str | None = None, category: str | None = None,
    category_id: UUID | None = None, kind: str | None = None, tag: str | None = None, tenant_id: UUID | None = None,
    enterprise_id: UUID | None = None, source: str | None = None, page: int = 1, page_size: int = 20, public_only: bool = True,
    module: str | None = None,
) -> BlogPaginatedResponse:
    """Live posts, pinned first and then newest first."""
    query = blog_repo.filter_posts(
        blog_repo.public_posts(db), search=search, kind=kind, category=category, category_id=category_id, tag=tag,
        tenant_id=tenant_id, enterprise_id=enterprise_id, source=source, module=module,
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
    """A live post, by slug (or by id). A post that is not live is a 404 for the public. Never moderation notes or a waiting edit."""
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
    form = forms.resolve_pinned(db, post.form_id, post.form_version)
    return BlogDetailResponse.model_validate({**_public_dict(post), "content": post.content, "author_id": None, "created_by": None, "updated_by": None,
                                              **_public_extras(form, _main_content(post))})


# ---------------------------------------------------------------- merchant

def _business_for(db: Session, actor: BlogActor, enterprise_id: UUID | None):
    from app.models.enterprise_model import Enterprise

    query = db.query(Enterprise).filter(Enterprise.tenant_id == actor.tenant_id, Enterprise.is_deleted.is_(False))
    if enterprise_id is not None:
        row = query.filter(Enterprise.id == enterprise_id).first()
        if row is None:
            raise problem(403, "That business does not belong to your account", "FORBIDDEN")
        return row
    row = query.order_by(Enterprise.created_at).first()
    if row is None:
        raise _unprocessable("Your account has no business profile yet. Create the business before writing posts", "NO_BUSINESS")
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


def my_post_for_preview(db: Session, actor: BlogActor, post_id: UUID) -> BlogPost:
    return _my_post(db, actor, post_id)


def admin_post_for_preview(db: Session, post_id: UUID) -> BlogPost:
    return _admin_post(db, post_id)


def create_my_post_service(db: Session, actor: BlogActor, payload) -> BlogManageDetail:
    governance.assert_enabled(db, payload.module)
    enterprise = _business_for(db, actor, payload.enterprise_id)
    fields, form = _prepare(db, actor, None, payload, tenant_id=actor.tenant_id, source="merchant")
    post = BlogPost(
        tenant_id=actor.tenant_id, source="merchant", module=payload.module, status="draft", is_live=False, author_id=UUID(actor.id),
        author_name=actor.name, created_by=actor.id, updated_by=actor.id, kind=fields.pop("kind", "blog"),
        slug=_unique_slug(db, fields["title"]), content="", tags=[], version=1,
    )
    for key, value in fields.items():
        setattr(post, key, value)
    _bind(post, form)
    _stamp_business(post, enterprise)
    _set_main(db, post, {})
    db.add(post)
    db.flush()
    _log(db, post, "created", actor, to_status="draft", bump=False)
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


def my_post_revisions_service(db: Session, actor: BlogActor, post_id: UUID) -> BlogRevisionList:
    return _revisions_for(db, _my_post(db, actor, post_id))


def my_post_revision_service(db: Session, actor: BlogActor, post_id: UUID, history_id: UUID) -> BlogRevisionDetail:
    return _revision_detail(db, _my_post(db, actor, post_id), history_id)


def update_my_post_service(db: Session, actor: BlogActor, post_id: UUID, payload, expected: int | None = None) -> BlogManageDetail:
    post = _my_post(db, actor, post_id, lock=True)
    _expect(post, payload.base_version if payload.base_version is not None else expected)
    if post.status == "pending_approval":
        raise _conflict("This post is waiting for review. You can edit it again once it has been decided", "IN_REVIEW")
    if post.status not in MERCHANT_EDITABLE:
        raise _conflict(f"A post that is {STATUS_LABELS[post.status].lower()} cannot be edited")
    current = _working(post)
    changes, form = _prepare(db, actor, post, payload, tenant_id=post.tenant_id, source="merchant")
    if payload.enterprise_id is not None and not post.is_live:
        _stamp_business(post, _business_for(db, actor, payload.enterprise_id))
    if not changes:
        db.commit()
        return _manage_detail(db, post, actor)
    previous = post.status
    if post.is_live:
        # the live version stays up; the edit waits in pending_revision
        merged = {**current, **changes}
        _ready(db, post, merged, "send this edit for approval")
        post.pending_revision = _json_content(merged)
        if post.status == "published":
            post.status, post.submitted_at, post.reviewer_note = "pending_approval", _now(), None
            post.updated_by = actor.id
            _log(db, post, "edited", actor, from_status=previous)
            _log(db, post, "submitted", actor, from_status=previous, to_status="pending_approval", bump=False)
            db.commit()
            blog_notifications.notify_submitted(post.id, access_token=actor.token, is_edit=True)
            db.refresh(post)
            return _manage_detail(db, post, actor)
    else:
        _set_main(db, post, changes)
        _bind(post, form)
    post.updated_by = actor.id
    _log(db, post, "edited", actor, from_status=previous, to_status=post.status)
    db.commit()
    db.refresh(post)
    return _manage_detail(db, post, actor)


def submit_my_post_service(db: Session, actor: BlogActor, post_id: UUID, expected: int | None = None) -> BlogManageDetail:
    post = _my_post(db, actor, post_id, lock=True)
    _expect(post, expected)
    if post.status not in MERCHANT_SUBMITTABLE:
        raise _conflict(f"A post that is {STATUS_LABELS[post.status].lower()} cannot be sent for approval")
    _ready(db, post, _working(post), "send this for approval")
    previous = post.status
    post.status, post.submitted_at, post.reviewer_note, post.updated_by = "pending_approval", _now(), None, actor.id
    _log(db, post, "submitted", actor, from_status=previous, to_status="pending_approval")
    db.commit()
    blog_notifications.notify_submitted(post.id, access_token=actor.token, is_edit=bool(post.is_live))
    db.refresh(post)
    return _manage_detail(db, post, actor)


def discard_my_changes_service(db: Session, actor: BlogActor, post_id: UUID, expected: int | None = None) -> BlogManageDetail:
    """Drop an edit of a live post that was sent back, so the post is just published again."""
    post = _my_post(db, actor, post_id, lock=True)
    _expect(post, expected)
    if not (post.pending_revision and post.status in ("changes_requested", "rejected")):
        raise _conflict("There are no sent-back changes to discard", "NOTHING_TO_DISCARD")
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


def unpublish_my_post_service(db: Session, actor: BlogActor, post_id: UUID, reason: str | None, expected: int | None = None) -> BlogManageDetail:
    post = _my_post(db, actor, post_id, lock=True)
    _expect(post, expected)
    if not post.is_live:
        raise _conflict("This post is not live", "NOT_LIVE")
    _take_down(db, post, actor, _reason(reason, required=False, what="unpublish"))
    db.commit()
    db.refresh(post)
    return _manage_detail(db, post, actor)


def delete_my_post_service(db: Session, actor: BlogActor, post_id: UUID, expected: int | None = None) -> None:
    post = _my_post(db, actor, post_id, lock=True)
    _expect(post, expected)
    if post.status != "draft":
        raise _conflict("Only a draft can be deleted. Unpublish a live post instead", "ONLY_DRAFT_DELETABLE")
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
    governance.assert_enabled(db, payload.module)
    fields, form = _prepare(db, actor, None, payload, tenant_id=None, source="platform")
    slug = _explicit_slug(db, payload.slug) if payload.slug else _unique_slug(db, fields["title"])
    post = BlogPost(
        tenant_id=payload.tenant_id, enterprise_id=payload.enterprise_id, source="platform", module=payload.module, status="draft", is_live=False,
        author_id=UUID(actor.id), author_name=payload.author_name or actor.name or getattr(settings, "PLATFORM_NAME", None) or "Platform",
        created_by=actor.id, updated_by=actor.id, kind=fields.pop("kind", "blog"), slug=slug, content="", tags=[], version=1,
    )
    for key, value in fields.items():
        setattr(post, key, value)
    _bind(post, form)
    _set_main(db, post, {})
    db.add(post)
    db.flush()
    _log(db, post, "created", actor, to_status="draft", bump=False)
    if payload.status == "published":
        _ready(db, post, _main_content(post), "publish this")
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


def admin_post_revisions_service(db: Session, post_id: UUID) -> BlogRevisionList:
    return _revisions_for(db, _admin_post(db, post_id))


def admin_post_revision_service(db: Session, post_id: UUID, history_id: UUID) -> BlogRevisionDetail:
    return _revision_detail(db, _admin_post(db, post_id), history_id)


def update_blog_service(db: Session, post_id: UUID, payload, actor: BlogActor, expected: int | None = None) -> BlogManageDetail:
    """A Super Admin's edit applies at once, to a live post too. If the merchant's edit is waiting, decide that first."""
    post = _admin_post(db, post_id, lock=True)
    _expect(post, payload.base_version if payload.base_version is not None else expected)
    if post.pending_revision:
        raise _conflict("The merchant has an edit waiting for approval. Approve or reject it before editing this post", "PENDING_REVISION")
    changes, form = _prepare(db, actor, post, payload, tenant_id=post.tenant_id, source=post.source)
    data = payload.model_dump(exclude_unset=True)
    if data.get("slug"):
        post.slug = _explicit_slug(db, data["slug"], exclude_id=post.id)
    if data.get("author_name") and post.source == "platform":
        post.author_name = data["author_name"].strip()
    if changes:
        _set_main(db, post, changes)
        if post.is_live:
            _ready(db, post, _main_content(post), "save this live post")
        else:
            _bind(post, form)
    post.updated_by = actor.id
    _log(db, post, "edited", actor, to_status=post.status)
    db.commit()
    db.refresh(post)
    return _manage_detail(db, post, actor)


def publish_blog_service(db: Session, post_id: UUID, actor: BlogActor, expected: int | None = None) -> BlogManageDetail:
    post = _admin_post(db, post_id, lock=True)
    _expect(post, expected)
    if post.status == "pending_approval":
        raise _conflict("This post is waiting for review. Approve it from the approval queue", "IN_REVIEW")
    if post.status not in ADMIN_PUBLISHABLE:
        raise _conflict(f"A post that is {STATUS_LABELS[post.status].lower()} cannot be published")
    _ready(db, post, _main_content(post), "publish this")
    previous = post.status
    _go_live(post, actor)
    _log(db, post, "published", actor, from_status=previous, to_status="published")
    db.commit()
    blog_notifications.notify_decision(post.id, "published")
    db.refresh(post)
    return _manage_detail(db, post, actor)


def _needs_reason(post: BlogPost) -> bool:
    return post.source == "merchant"


def unpublish_blog_service(db: Session, post_id: UUID, actor: BlogActor, reason: str | None, expected: int | None = None) -> BlogManageDetail:
    post = _admin_post(db, post_id, lock=True)
    _expect(post, expected)
    if not post.is_live:
        raise _conflict("This post is not live", "NOT_LIVE")
    why = _reason(reason, required=_needs_reason(post), what="take down a merchant's post")
    _take_down(db, post, actor, why)
    db.commit()
    if post.source == "merchant":
        blog_notifications.notify_decision(post.id, "unpublished", reason=why)
    db.refresh(post)
    return _manage_detail(db, post, actor)


def delete_blog_service(db: Session, post_id: UUID, actor: BlogActor, reason: str | None, expected: int | None = None) -> None:
    """A Super Admin always gives a reason: it is kept in the post's history."""
    post = _admin_post(db, post_id, lock=True)
    _expect(post, expected)
    why = _reason(reason, required=True, what="delete a post")
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
        raise _conflict("This post is waiting for review", "IN_REVIEW")
    if post.is_live:
        why = _reason(reason, required=_needs_reason(post), what="take down a merchant's post")
        _take_down(db, post, actor, why)
    previous = post.status
    post.status = "draft"
    _log(db, post, "edited", actor, from_status=previous, to_status="draft", reason="Moved back to draft")
    db.commit()
    db.refresh(post)
    return _manage_detail(db, post, actor)


def review_blog_service(db: Session, post_id: UUID, actor: BlogActor, action: str, reason: str | None, expected: int | None = None) -> BlogManageDetail:
    post = _admin_post(db, post_id, lock=True)
    _expect(post, expected)
    if post.status != "pending_approval":
        raise _conflict(f"Only a post that is pending approval can be reviewed. This one is {STATUS_LABELS[post.status].lower()}", "NOT_PENDING_APPROVAL")
    if post.source == "merchant" and post.author_id is not None and str(post.author_id) == actor.id:
        raise problem(403, "You cannot review your own post", "SELF_REVIEW")
    why = _reason(reason, required=action != "approve", what="request changes" if action == "request_changes" else "reject a post")
    if action == "approve":
        if post.pending_revision:
            _set_main(db, post, _working(post))
            post.pending_revision = None
        _ready(db, post, _main_content(post), "approve this")
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


def pin_blog_service(db: Session, post_id: UUID, actor: BlogActor, pinned: bool, expected: int | None = None) -> BlogManageDetail:
    post = _admin_post(db, post_id, lock=True)
    _expect(post, expected)
    if pinned and not post.is_live:
        raise _conflict("Only a live post can be pinned", "NOT_LIVE")
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
        raise _unprocessable("The category name cannot be empty", "VALIDATION_FAILED", "name")
    return text


def create_category_service(db: Session, payload) -> BlogCategoryResponse:
    name = _clean_name(payload.name)
    if blog_repo.get_category_by_name(db, name):
        raise _conflict(f'A category named "{name}" already exists', "DUPLICATE_CATEGORY", field="name")
    top = db.query(func.max(BlogCategory.sort_order)).scalar()
    row = BlogCategory(name=name, name_key=name.lower(), is_active=payload.is_active, sort_order=(top + 1) if top is not None else 0)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _category_dict(row, 0)


def update_category_service(db: Session, category_id: UUID, payload) -> BlogCategoryResponse:
    row = blog_repo.get_category(db, category_id)
    if row is None:
        raise problem(404, "Category not found", "CATEGORY_NOT_FOUND")
    if payload.name is not None:
        name = _clean_name(payload.name)
        clash = blog_repo.get_category_by_name(db, name)
        if clash is not None and clash.id != row.id:
            raise _conflict(f'A category named "{name}" already exists', "DUPLICATE_CATEGORY", field="name")
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
        raise _unprocessable("Send every category id exactly once, in the order they should appear", "INVALID_ORDER", "ids")
    for position, category_id in enumerate(ids):
        rows[category_id].sort_order = position
    db.commit()
    return list_categories_service(db, public=False)


def delete_category_service(db: Session, category_id: UUID) -> None:
    row = blog_repo.get_category(db, category_id)
    if row is None:
        raise problem(404, "Category not found", "CATEGORY_NOT_FOUND")
    if db.query(func.count(BlogPost.id)).filter(BlogPost.category_id == row.id).scalar():
        raise _conflict("This category has posts. Switch it off instead of deleting it", "CATEGORY_IN_USE")
    db.delete(row)
    db.commit()
