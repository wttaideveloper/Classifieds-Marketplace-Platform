from uuid import UUID

from fastapi import APIRouter, Body, Depends, File, Form, Header, Path, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.blog_access import BlogActor, optional_blog_actor, require_blog_author, require_blog_merchant, require_blog_super_admin
from app.db.database import get_db
from app.exceptions.problem import problem
from app.repository.query_utils import build_pagination_meta
from app.schemas.blog_form_schema import (
    ActiveFormsForBusiness, FormAssign, FormCreate, FormDetail, FormItem, FormPage, FormPreview, FormPreviewRequest, FormPublish,
    FormScope, FormType, FormUpdate, FormVersionDetail, FormVersionList, ResolvedFormOut,
)
from app.schemas.blog_schema import (
    AdminPostCreate, AdminPostUpdate, BlogCategoryListResponse, BlogCategoryResponse, BlogDetailResponse, BlogHistoryResponse,
    BlogKind, BlogManageDetail, BlogManagePaginatedResponse, BlogPaginatedResponse, BlogPreviewResponse, BlogRevisionDetail,
    BlogRevisionList, BlogSource, BlogStatusUpdate, CategoryCreate, CategoryReorder, CategoryUpdate, FileUploadResponse,
    MerchantPostCreate, MerchantPostUpdate, PinRequest, PreviewRequest, ReasonRequest, ReviewRequest, UploadLimits,
)
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.services import blog_form_service as form_svc
from app.services import blog_upload_service as upload_svc
from app.services.blog_service import (
    admin_post_for_preview, admin_post_history_service, admin_post_revision_service, admin_post_revisions_service, approval_queue_service,
    create_blog_service, create_category_service, create_my_post_service, delete_blog_service, delete_category_service,
    delete_my_post_service, discard_my_changes_service, get_admin_post_service, get_blog_service, get_my_post_service, list_admin_posts_service,
    list_blogs_service, list_categories_service, list_my_posts_service, my_post_for_preview, my_post_history_service, my_post_revision_service,
    my_post_revisions_service, pin_blog_service, preview_payload_service, preview_post_service, publish_blog_service, reorder_categories_service,
    review_blog_service, submit_my_post_service, unpublish_blog_service, unpublish_my_post_service, update_blog_service,
    update_blog_status_service, update_category_service, update_my_post_service,
)

router = APIRouter(tags=["CMS — Blogs"])

PAGE = Query(DEFAULT_PAGE, ge=1)
PAGE_SIZE = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
IF_MATCH_DOC = (
    " Send `If-Match: \"<version>\"` (or `base_version` in the body) with the `version` you loaded; if the post has changed since, the answer is "
    "409 `VERSION_CONFLICT` with `current_version`, and nothing is changed."
)


def if_match(value: str | None = Header(None, alias="If-Match", description="The post's `version` as returned in its `ETag`, to refuse a change made on an old copy")) -> int | None:
    if value is None or value.strip() in ("", "*"):
        return None
    text = value.strip()
    if text[:2].upper() == "W/":
        text = text[2:]
    try:
        return int(text.strip('"'))
    except ValueError:
        raise problem(400, "If-Match must be the post's version, for example \"3\"", "INVALID_IF_MATCH")


def tagged(response: Response, detail):
    """Puts the post's version in the ETag header too."""
    response.headers["ETag"] = f'"{getattr(detail, "version", 1)}"'
    return detail


# ---------------------------------------------------------------- public (live posts only)

@router.get(
    "", response_model=BlogPaginatedResponse, summary="List blogs and news (public)",
    description="Live posts only: pinned first, then newest first. `kind=blog` is the Blogs list, `kind=news` the News list. "
                "Paged with `page` and `page_size`; the answer is `{items, pagination: {total, page, page_size, total_pages}}`.",
)
def list_public_blogs(
    kind: BlogKind | None = Query(None, description="blog | news"),
    search: str | None = Query(None, description="Matches title, summary, body, author and business name"),
    category_id: UUID | None = Query(None), category: str | None = Query(None, description="Category name"),
    tag: str | None = Query(None), source: BlogSource | None = Query(None, description="platform | merchant"),
    tenant_id: UUID | None = Query(None), enterprise_id: UUID | None = Query(None),
    page: int = PAGE, page_size: int = PAGE_SIZE, db: Session = Depends(get_db),
):
    return list_blogs_service(
        db, search=search, category=category, category_id=category_id, kind=kind, tag=tag, tenant_id=tenant_id,
        enterprise_id=enterprise_id, source=source, page=page, page_size=page_size, public_only=True,
    )


@router.get(
    "/categories", response_model=BlogCategoryListResponse, summary="Categories for the filter (public)",
    description="Active categories in their set order, with the number of live posts in each (`kind` narrows the count).",
)
def list_public_categories(kind: BlogKind | None = Query(None), db: Session = Depends(get_db)):
    return list_categories_service(db, public=True, kind=kind)


@router.get(
    "/files/{file_id}", summary="An uploaded image or document",
    description="Public while a live post uses the file. Otherwise only the business that uploaded it and Super Admins can fetch it (sign in); anyone else gets 404 `FILE_NOT_FOUND`. "
                "Images are shown inline, documents download.",
    responses={200: {"content": {"image/*": {}, "application/pdf": {}}}},
)
def get_file(file_id: UUID = Path(...), db: Session = Depends(get_db), actor: BlogActor | None = Depends(optional_blog_actor)):
    path, row = upload_svc.open_file(db, file_id, actor)
    is_image = row.content_type.startswith("image/")
    return FileResponse(path=str(path), media_type=row.content_type, filename=row.original_name or path.name,
                        content_disposition_type="inline" if is_image else "attachment",
                        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})


# ---------------------------------------------------------------- uploads (Super Admin and Enterprise Admin)

@router.get("/uploads/limits", response_model=UploadLimits, summary="What may be uploaded (Super Admin, Enterprise Admin)",
            description="The size limits, accepted types and counts, so the form can check before it uploads.")
def upload_limits(_actor: BlogActor = Depends(require_blog_author)):
    return upload_svc.limits()


@router.post(
    "/uploads", response_model=FileUploadResponse, status_code=status.HTTP_201_CREATED, summary="Upload a cover image, an image or a document",
    description=(
        "`multipart/form-data` with `file` and `purpose`: `cover_image` or `image` (JPEG, PNG, WebP or GIF, up to BLOG_MAX_IMAGE_MB, default 5 MB) or "
        "`document` (PDF, DOC, DOCX, XLS, XLSX, PPT, PPTX, TXT or CSV, up to BLOG_MAX_DOCUMENT_MB, default 10 MB). The type is read from the content. "
        "Use the returned `url` in `cover_image_url`, `additional_images[].url` or `supporting_documents[].url`. Errors: 413 `FILE_TOO_LARGE`, 415 `UNSUPPORTED_TYPE`, "
        "422 `EMPTY_FILE` / `INVALID_FILE` / `VIRUS_DETECTED`, 503 `SCANNER_UNAVAILABLE`."
    ),
)
def upload_file(file: UploadFile = File(...), purpose: str = Form(..., description="cover_image | image | document"),
                db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_author)):
    return upload_svc.store(db, actor, file, purpose)


# ---------------------------------------------------------------- merchant: the form, and the business's own posts

@router.get("/my/form", response_model=ResolvedFormOut, summary="The form to write a post with (Enterprise Admin)",
            description="The active form assigned to the caller's business for this kind of post, with its sections and fields. A form assigned to the business wins over a global one. "
                        "`form_id: null` and `version: 0` mean the built-in Standard form.")
def my_form(kind: BlogKind = Query(..., description="blog | news"), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_merchant)):
    return form_svc.resolve_active(db, kind, actor.tenant_id).summary()


@router.get("/my/posts", response_model=BlogManagePaginatedResponse, summary="My posts (Enterprise Admin)",
            description="Every post of the caller's business with its status and the reviewer's note. Deleted posts are not listed.")
def list_my_posts(
    status_filter: str | None = Query(None, alias="status"), kind: BlogKind | None = Query(None), category_id: UUID | None = Query(None),
    search: str | None = Query(None), page: int = PAGE, page_size: int = PAGE_SIZE,
    db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_merchant),
):
    return list_my_posts_service(db, actor, status_filter=status_filter, kind=kind, category_id=category_id, search=search, page=page, page_size=page_size)


@router.post("/my/posts", response_model=BlogManageDetail, status_code=status.HTTP_201_CREATED, summary="Create a draft (Enterprise Admin)",
             description="Saves a draft for the caller's business, written with the active form (its `form_version` is on the answer). Send it for approval with POST /my/posts/{id}/submit. "
                         "409 `DUPLICATE_TITLE` when the business already has a post of that kind with the title.")
def create_my_post(response: Response, payload: MerchantPostCreate = Body(...), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_merchant)):
    return tagged(response, create_my_post_service(db, actor, payload))


@router.post("/my/posts/preview", response_model=BlogPreviewResponse, summary="Preview an unsaved post (Enterprise Admin)",
             description="Shows the post as a reader would see it, from what is sent; nothing is stored. `ready_to_submit` and `problems` say what is still missing for the active form.")
def preview_unsaved_my_post(payload: PreviewRequest = Body(...), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_merchant)):
    return preview_payload_service(db, actor, payload)


@router.get("/my/posts/{post_id}", response_model=BlogManageDetail, summary="One of my posts (Enterprise Admin)",
            description="Includes `version` (also in the ETag header), the form with its fields, custom field values, and `pending_revision` / `live_version` while an edit of a live post waits.")
def get_my_post(response: Response, post_id: UUID = Path(...), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_merchant)):
    return tagged(response, get_my_post_service(db, actor, post_id))


@router.get("/my/posts/{post_id}/preview", response_model=BlogPreviewResponse, summary="Preview my saved post (Enterprise Admin)",
            description="The latest saved version, a waiting edit included, as a reader would see it.")
def preview_my_post(post_id: UUID = Path(...), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_merchant)):
    return preview_post_service(db, actor, my_post_for_preview(db, actor, post_id))


@router.patch(
    "/my/posts/{post_id}", response_model=BlogManageDetail, summary="Edit my post (Enterprise Admin)",
    description=(
        "Allowed while the post is a draft, changes requested, rejected, unpublished or published. Editing a **published** post sends the edit "
        "for approval at once: the post goes to pending_approval but the old version stays live until the edit is approved. "
        "A post that is pending approval cannot be edited (409 `IN_REVIEW`)." + IF_MATCH_DOC
    ),
)
def update_my_post(response: Response, post_id: UUID, payload: MerchantPostUpdate = Body(...), expected: int | None = Depends(if_match),
                   db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_merchant)):
    return tagged(response, update_my_post_service(db, actor, post_id, payload, expected))


@router.post("/my/posts/{post_id}/submit", response_model=BlogManageDetail, summary="Send my post for approval (Enterprise Admin)",
             description="Also how a post is sent again after changes were requested or it was rejected. Needs everything the form requires (422 `VALIDATION_FAILED` with `errors`). "
                         "Super Admins are notified." + IF_MATCH_DOC)
def submit_my_post(response: Response, post_id: UUID, expected: int | None = Depends(if_match), db: Session = Depends(get_db),
                   actor: BlogActor = Depends(require_blog_merchant)):
    return tagged(response, submit_my_post_service(db, actor, post_id, expected))


@router.post("/my/posts/{post_id}/unpublish", response_model=BlogManageDetail, summary="Take my live post down (Enterprise Admin)")
def unpublish_my_post(response: Response, post_id: UUID, payload: ReasonRequest = Body(default_factory=ReasonRequest), expected: int | None = Depends(if_match),
                      db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_merchant)):
    return tagged(response, unpublish_my_post_service(db, actor, post_id, payload.reason, payload.base_version if payload.base_version is not None else expected))


@router.post("/my/posts/{post_id}/discard-changes", response_model=BlogManageDetail, summary="Drop sent-back changes of a live post (Enterprise Admin)",
             description="When an edit of a live post was rejected or sent back, this drops the edit so the post is simply published again.")
def discard_my_changes(response: Response, post_id: UUID, expected: int | None = Depends(if_match), db: Session = Depends(get_db),
                       actor: BlogActor = Depends(require_blog_merchant)):
    return tagged(response, discard_my_changes_service(db, actor, post_id, expected))


@router.delete("/my/posts/{post_id}", summary="Delete my draft (Enterprise Admin)", description="Drafts only (409 `ONLY_DRAFT_DELETABLE` otherwise). A live post is unpublished instead.")
def delete_my_post(post_id: UUID, expected: int | None = Depends(if_match), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_merchant)):
    delete_my_post_service(db, actor, post_id, expected)
    return {"message": "Blog post deleted"}


@router.get("/my/posts/{post_id}/history", response_model=BlogHistoryResponse, summary="History of my post (Enterprise Admin)",
            description="Every change with its action, old and new status, who did it and their role, the reason, the time and the post version after it.")
def my_post_history(post_id: UUID, db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_merchant)):
    return my_post_history_service(db, actor, post_id)


@router.get("/my/posts/{post_id}/revisions", response_model=BlogRevisionList, summary="Revisions of my post (Enterprise Admin)",
            description="The changes that kept a copy of the content (created, edited, submitted, approved, published), newest first.")
def my_post_revisions(post_id: UUID, db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_merchant)):
    return my_post_revisions_service(db, actor, post_id)


@router.get("/my/posts/{post_id}/revisions/{history_id}", response_model=BlogRevisionDetail, summary="One revision with its content (Enterprise Admin)")
def my_post_revision(post_id: UUID, history_id: UUID, db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_merchant)):
    return my_post_revision_service(db, actor, post_id, history_id)


# ---------------------------------------------------------------- Super Admin: forms (registered before /{slug})

@router.get("/admin/forms", response_model=FormPage, summary="Forms (Super Admin)",
            description="Newest edit first. Filter by `form_type`, `status` (draft | published), `is_active`, `scope`, `tenant_id` (forms that business can get) and `search` (name).")
def list_forms(
    form_type: FormType | None = Query(None), status_filter: str | None = Query(None, alias="status", pattern="^(draft|published)$"),
    is_active: bool | None = Query(None), scope: FormScope | None = Query(None), tenant_id: UUID | None = Query(None), search: str | None = Query(None),
    page: int = PAGE, page_size: int = PAGE_SIZE, db: Session = Depends(get_db), _actor: BlogActor = Depends(require_blog_super_admin),
):
    items, total = form_svc.list_forms(db, form_type=form_type, status=status_filter, is_active=is_active, scope=scope, search=search, tenant_id=tenant_id,
                                       page=page, page_size=page_size)
    return FormPage(items=[FormItem.model_validate(i) for i in items], pagination=build_pagination_meta(total, page, page_size))


@router.post("/admin/forms", response_model=FormDetail, status_code=status.HTTP_201_CREATED, summary="Create a form (Super Admin)",
             description="Saved as a draft: posts do not use it until it is published. `mode: standard` is the built-in fields (nothing to configure); `mode: configurable` needs a `definition`. "
                         "422 `INVALID_FORM` lists every problem in `errors`; 409 `DUPLICATE_FORM_NAME`.")
def create_form(payload: FormCreate = Body(...), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin)):
    return form_svc.create_form(db, actor, payload)


@router.post("/admin/forms/preview", response_model=FormPreview, summary="Preview a form that is not saved yet (Super Admin)",
             description="Checks a definition and returns it cleaned and in order, as the writer would see it. Nothing is stored.")
def preview_unsaved_form(payload: FormPreviewRequest = Body(...), db: Session = Depends(get_db), _actor: BlogActor = Depends(require_blog_super_admin)):
    return form_svc.preview(db, None, payload.definition, payload.mode, payload.name)


@router.get("/admin/forms/active", response_model=ActiveFormsForBusiness, summary="The active forms a business gets (Super Admin)",
            description="The blog form and the news form an Enterprise Admin would be given right now. Without `tenant_id`, what platform posts use (global forms only).")
def active_forms(tenant_id: UUID | None = Query(None), db: Session = Depends(get_db), _actor: BlogActor = Depends(require_blog_super_admin)):
    return form_svc.active_for_tenant(db, tenant_id)


@router.get("/admin/forms/{form_id}", response_model=FormDetail, summary="One form with its draft fields and assigned businesses (Super Admin)")
def get_form(form_id: UUID, db: Session = Depends(get_db), _actor: BlogActor = Depends(require_blog_super_admin)):
    return form_svc.get_form_dict(db, form_id)


@router.patch("/admin/forms/{form_id}", response_model=FormDetail, summary="Edit a form's draft (Super Admin)",
              description="Changes the draft only; posts are not affected until the form is published. Send `base_revision` (the `revision` you loaded) to refuse a save over someone else's: 409 `VERSION_CONFLICT`.")
def update_form(form_id: UUID, payload: FormUpdate = Body(...), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin)):
    return form_svc.update_form(db, actor, form_id, payload)


@router.put("/admin/forms/{form_id}/assignments", response_model=FormDetail, summary="Make a form global or give it to chosen businesses (Super Admin)",
            description="`scope: global` = every business. `scope: selected` = only the businesses in `tenant_ids`. Takes effect at once for new and not-live posts; live posts keep their version.")
def assign_form(form_id: UUID, payload: FormAssign = Body(...), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin)):
    return form_svc.assign_form(db, actor, form_id, payload.scope, payload.tenant_ids)


@router.post("/admin/forms/{form_id}/publish", response_model=FormDetail, summary="Publish the draft as a new version (Super Admin)",
             description="Freezes the draft as the next version. The first time, the form also becomes active. Posts already live keep the version they were written with.")
def publish_form(form_id: UUID, payload: FormPublish = Body(default_factory=FormPublish), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin)):
    return form_svc.publish_form(db, actor, form_id, payload.note, payload.base_revision)


@router.post("/admin/forms/{form_id}/activate", response_model=FormDetail, summary="Activate a published form (Super Admin)",
             description="New posts may use it. 409 `FORM_NOT_PUBLISHED` for a form that was never published.")
def activate_form(form_id: UUID, db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin)):
    return form_svc.set_active(db, actor, form_id, True)


@router.post("/admin/forms/{form_id}/deactivate", response_model=FormDetail, summary="Stop offering a form (Super Admin)",
             description="New posts no longer get it. Posts written with it keep their version and stay readable.")
def deactivate_form(form_id: UUID, db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin)):
    return form_svc.set_active(db, actor, form_id, False)


@router.get("/admin/forms/{form_id}/preview", response_model=FormPreview, summary="Preview a form's draft (Super Admin)")
def preview_form(form_id: UUID, db: Session = Depends(get_db), _actor: BlogActor = Depends(require_blog_super_admin)):
    return form_svc.preview(db, form_id, None, None, None)


@router.get("/admin/forms/{form_id}/versions", response_model=FormVersionList, summary="Published versions of a form (Super Admin)")
def form_versions(form_id: UUID, db: Session = Depends(get_db), _actor: BlogActor = Depends(require_blog_super_admin)):
    items = form_svc.list_versions(db, form_id)
    return FormVersionList(items=items, total=len(items))


@router.get("/admin/forms/{form_id}/versions/{version}", response_model=FormVersionDetail, summary="One published version with its fields (Super Admin)",
            description="Exactly what posts of that version were written with. Never changes.")
def form_version(form_id: UUID, version: int = Path(..., ge=1), db: Session = Depends(get_db), _actor: BlogActor = Depends(require_blog_super_admin)):
    return form_svc.get_version_dict(db, form_id, version)


@router.delete("/admin/forms/{form_id}", summary="Delete a form (Super Admin)",
               description="Refused with 409 `FORM_IN_USE` (and `post_count`) while any post was written with it. Deactivate it instead.")
def delete_form(form_id: UUID, db: Session = Depends(get_db), _actor: BlogActor = Depends(require_blog_super_admin)):
    form_svc.delete_form(db, form_id)
    return {"message": "Form deleted"}


# ---------------------------------------------------------------- Super Admin: posts and categories

@router.get("/admin/queue", response_model=BlogManagePaginatedResponse, summary="Approval queue (Super Admin)",
            description="Posts pending approval, oldest first.")
def approval_queue(
    kind: BlogKind | None = Query(None), category_id: UUID | None = Query(None), tenant_id: UUID | None = Query(None),
    enterprise_id: UUID | None = Query(None, description="The business"), search: str | None = Query(None),
    page: int = PAGE, page_size: int = PAGE_SIZE, db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin),
):
    return approval_queue_service(db, actor, kind=kind, category_id=category_id, tenant_id=tenant_id, enterprise_id=enterprise_id,
                                  search=search, page=page, page_size=page_size)


@router.get("/admin/posts", response_model=BlogManagePaginatedResponse, summary="All posts (Super Admin)",
            description="Every status except deleted (ask for `status=deleted` to see those).")
def list_admin_blogs(
    status_filter: str | None = Query(None, alias="status"), source: BlogSource | None = Query(None), kind: BlogKind | None = Query(None),
    category_id: UUID | None = Query(None), category: str | None = Query(None), tag: str | None = Query(None),
    search: str | None = Query(None, description="Title, summary, body, author, business"), tenant_id: UUID | None = Query(None),
    enterprise_id: UUID | None = Query(None), pinned: bool | None = Query(None), page: int = PAGE, page_size: int = PAGE_SIZE,
    db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin),
):
    return list_admin_posts_service(
        db, actor, status_filter=status_filter, source=source, kind=kind, category_id=category_id, category=category, tag=tag,
        search=search, tenant_id=tenant_id, enterprise_id=enterprise_id, pinned=pinned, page=page, page_size=page_size,
    )


@router.post("/admin/posts", response_model=BlogManageDetail, status_code=status.HTTP_201_CREATED, summary="Create a platform post (Super Admin)",
             description="`status: published` makes it live straight away, with no approval step. Needs a category and a body to publish. Written with the active global form. "
                         "409 `DUPLICATE_SLUG` for a `slug` that is taken; 409 `DUPLICATE_TITLE`.")
def create_admin_blog(response: Response, payload: AdminPostCreate = Body(...), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin)):
    return tagged(response, create_blog_service(db, payload, actor))


@router.post("/admin/posts/preview", response_model=BlogPreviewResponse, summary="Preview an unsaved platform post (Super Admin)",
             description="Nothing is stored.")
def preview_unsaved_admin_post(payload: PreviewRequest = Body(...), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin)):
    return preview_payload_service(db, actor, payload)


@router.get("/admin/posts/{post_id}", response_model=BlogManageDetail, summary="Get a post (Super Admin)",
            description="Everything about the post: content, form and custom values, author and business, dates, status, live version, pending edit, `version` (and ETag).")
def get_admin_blog(response: Response, post_id: UUID = Path(...), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin)):
    return tagged(response, get_admin_post_service(db, actor, post_id))


@router.get("/admin/posts/{post_id}/preview", response_model=BlogPreviewResponse, summary="Preview a saved post (Super Admin)",
            description="The latest saved version, a merchant's waiting edit included, as a reader would see it.")
def preview_admin_post(post_id: UUID = Path(...), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin)):
    return preview_post_service(db, actor, admin_post_for_preview(db, post_id))


@router.put("/admin/posts/{post_id}", response_model=BlogManageDetail, summary="Edit a post (Super Admin)",
            description="Applies at once, to a live post too. Refused (409 `PENDING_REVISION`) while a merchant's edit waits for approval; decide that first." + IF_MATCH_DOC)
def update_admin_blog(response: Response, post_id: UUID, payload: AdminPostUpdate = Body(...), expected: int | None = Depends(if_match),
                      db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin)):
    return tagged(response, update_blog_service(db, post_id, payload, actor, expected))


@router.patch("/admin/posts/{post_id}/status", response_model=BlogManageDetail, summary="Publish / unpublish / back to draft (Super Admin)",
              description="Kept for older clients. `archived` means `unpublished`. Prefer /publish, /unpublish and /review.")
def update_admin_blog_status(response: Response, post_id: UUID, payload: BlogStatusUpdate = Body(...), db: Session = Depends(get_db),
                             actor: BlogActor = Depends(require_blog_super_admin)):
    return tagged(response, update_blog_status_service(db, post_id, payload.status, actor, payload.reason))


@router.post("/admin/posts/{post_id}/publish", response_model=BlogManageDetail, summary="Publish a post now (Super Admin)",
             description="For a draft, unpublished, sent-back or rejected post. A post that is pending approval goes through /review." + IF_MATCH_DOC)
def publish_admin_blog(response: Response, post_id: UUID, expected: int | None = Depends(if_match), db: Session = Depends(get_db),
                       actor: BlogActor = Depends(require_blog_super_admin)):
    return tagged(response, publish_blog_service(db, post_id, actor, expected))


@router.post("/admin/posts/{post_id}/unpublish", response_model=BlogManageDetail, summary="Take a live post down (Super Admin)",
             description="A `reason` is required for a merchant's post; the merchant is notified with it.")
def unpublish_admin_blog(response: Response, post_id: UUID, payload: ReasonRequest = Body(default_factory=ReasonRequest), expected: int | None = Depends(if_match),
                         db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin)):
    return tagged(response, unpublish_blog_service(db, post_id, actor, payload.reason, payload.base_version if payload.base_version is not None else expected))


@router.post("/admin/posts/{post_id}/review", response_model=BlogManageDetail, summary="Approve, request changes or reject (Super Admin)",
             description=(
                 "For a post that is pending approval. `approve` publishes it (and applies a merchant's edit of a live post); "
                 "`request_changes` and `reject` need a `reason` (422 `REASON_REQUIRED`), which the merchant sees on the post and in the notification." + IF_MATCH_DOC
             ))
def review_admin_blog(response: Response, post_id: UUID, payload: ReviewRequest = Body(...), expected: int | None = Depends(if_match),
                      db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin)):
    return tagged(response, review_blog_service(db, post_id, actor, payload.action, payload.reason, payload.base_version if payload.base_version is not None else expected))


@router.put("/admin/posts/{post_id}/pin", response_model=BlogManageDetail, summary="Pin or unpin a post (Super Admin)",
            description="A pinned post stays at the top of its public list. One on/off switch. Only a live post can be pinned.")
def pin_admin_blog(response: Response, post_id: UUID, payload: PinRequest = Body(default_factory=PinRequest), expected: int | None = Depends(if_match),
                   db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin)):
    return tagged(response, pin_blog_service(db, post_id, actor, payload.pinned, payload.base_version if payload.base_version is not None else expected))


@router.delete("/admin/posts/{post_id}", status_code=status.HTTP_200_OK, summary="Delete a post (Super Admin)",
               description="Soft delete: the post is kept for the record but hidden everywhere. `reason` is always required (422 `REASON_REQUIRED`) and is kept in the history.")
def delete_admin_blog(post_id: UUID, reason: str | None = Query(None, max_length=500), expected: int | None = Depends(if_match),
                      db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin)):
    delete_blog_service(db, post_id, actor, reason, expected)
    return {"message": "Blog post deleted"}


@router.get("/admin/posts/{post_id}/history", response_model=BlogHistoryResponse, summary="Who changed what, and when (Super Admin)",
            description="The moderation history: action, old and new status, who, their role, the reason, the time and the post version after the change.")
def admin_blog_history(post_id: UUID, db: Session = Depends(get_db), _actor: BlogActor = Depends(require_blog_super_admin)):
    return admin_post_history_service(db, post_id)


@router.get("/admin/posts/{post_id}/revisions", response_model=BlogRevisionList, summary="Revisions of a post (Super Admin)",
            description="The changes that kept a copy of the content (created, edited, submitted, approved, published), newest first.")
def admin_blog_revisions(post_id: UUID, db: Session = Depends(get_db), _actor: BlogActor = Depends(require_blog_super_admin)):
    return admin_post_revisions_service(db, post_id)


@router.get("/admin/posts/{post_id}/revisions/{history_id}", response_model=BlogRevisionDetail, summary="One revision with its content (Super Admin)")
def admin_blog_revision(post_id: UUID, history_id: UUID, db: Session = Depends(get_db), _actor: BlogActor = Depends(require_blog_super_admin)):
    return admin_post_revision_service(db, post_id, history_id)


@router.get("/admin/categories", response_model=BlogCategoryListResponse, summary="All categories (Super Admin)",
            description="Including switched-off ones, with the number of posts in each.")
def list_admin_categories(db: Session = Depends(get_db), _actor: BlogActor = Depends(require_blog_super_admin)):
    return list_categories_service(db, public=False)


@router.post("/admin/categories", response_model=BlogCategoryResponse, status_code=status.HTTP_201_CREATED, summary="Create a category (Super Admin)")
def create_admin_category(payload: CategoryCreate = Body(...), db: Session = Depends(get_db), _actor: BlogActor = Depends(require_blog_super_admin)):
    return create_category_service(db, payload)


@router.put("/admin/categories/order", response_model=BlogCategoryListResponse, summary="Set the order of the categories (Super Admin)",
            description="Send every category id once, in the order they should appear.")
def reorder_admin_categories(payload: CategoryReorder = Body(...), db: Session = Depends(get_db), _actor: BlogActor = Depends(require_blog_super_admin)):
    return reorder_categories_service(db, payload.ids)


@router.patch("/admin/categories/{category_id}", response_model=BlogCategoryResponse, summary="Rename or switch a category on/off (Super Admin)")
def update_admin_category(category_id: UUID, payload: CategoryUpdate = Body(...), db: Session = Depends(get_db),
                          _actor: BlogActor = Depends(require_blog_super_admin)):
    return update_category_service(db, category_id, payload)


@router.delete("/admin/categories/{category_id}", summary="Delete an unused category (Super Admin)",
               description="A category that has posts (409 `CATEGORY_IN_USE`) can only be switched off.")
def delete_admin_category(category_id: UUID, db: Session = Depends(get_db), _actor: BlogActor = Depends(require_blog_super_admin)):
    delete_category_service(db, category_id)
    return {"message": "Category deleted"}


# ---------------------------------------------------------------- one live post (last, so it never shadows the paths above)

@router.get("/{slug}", response_model=BlogDetailResponse, summary="Get a live post by slug (public)",
            description="Counts one view. A post that is not live is a 404. The id also works in place of the slug. Never returns reviewer notes, a waiting edit or "
                        "internal fields; custom fields are only the ones the form marks public.")
def get_public_blog_by_slug(slug: str = Path(..., description="Post slug"), db: Session = Depends(get_db)):
    return get_blog_service(db, slug=slug, public_only=True, increment_views=True)
