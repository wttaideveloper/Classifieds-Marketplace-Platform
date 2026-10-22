from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.core.blog_access import BlogActor, require_blog_merchant, require_blog_super_admin
from app.db.database import get_db
from app.schemas.blog_schema import (
    AdminPostCreate, AdminPostUpdate, BlogCategoryListResponse, BlogCategoryResponse, BlogDetailResponse, BlogHistoryResponse,
    BlogKind, BlogManageDetail, BlogManagePaginatedResponse, BlogPaginatedResponse, BlogSource, BlogStatusUpdate,
    CategoryCreate, CategoryReorder, CategoryUpdate, MerchantPostCreate, MerchantPostUpdate, PinRequest, ReasonRequest, ReviewRequest,
)
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.services.blog_service import (
    admin_post_history_service, approval_queue_service, create_blog_service, create_category_service, create_my_post_service,
    delete_blog_service, delete_category_service, delete_my_post_service, discard_my_changes_service, get_admin_post_service,
    get_blog_service, get_my_post_service, list_admin_posts_service, list_blogs_service, list_categories_service,
    list_my_posts_service, my_post_history_service, pin_blog_service, publish_blog_service, reorder_categories_service,
    review_blog_service, submit_my_post_service, unpublish_blog_service, unpublish_my_post_service, update_blog_service,
    update_blog_status_service, update_category_service, update_my_post_service,
)

router = APIRouter(tags=["CMS — Blogs"])

PAGE = Query(DEFAULT_PAGE, ge=1)
PAGE_SIZE = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)


# ---------------------------------------------------------------- public (live posts only)

@router.get(
    "", response_model=BlogPaginatedResponse, summary="List blogs and news (public)",
    description="Live posts only: pinned first, then newest first. `kind=blog` is the Blogs list, `kind=news` the News list.",
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


# ---------------------------------------------------------------- merchant: the business's own posts

@router.get("/my/posts", response_model=BlogManagePaginatedResponse, summary="My posts (merchant)",
            description="Every post of the caller's business with its status and the reviewer's note. Deleted posts are not listed.")
def list_my_posts(
    status_filter: str | None = Query(None, alias="status"), kind: BlogKind | None = Query(None), category_id: UUID | None = Query(None),
    search: str | None = Query(None), page: int = PAGE, page_size: int = PAGE_SIZE,
    db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_merchant),
):
    return list_my_posts_service(db, actor, status_filter=status_filter, kind=kind, category_id=category_id, search=search, page=page, page_size=page_size)


@router.post("/my/posts", response_model=BlogManageDetail, status_code=status.HTTP_201_CREATED, summary="Create a draft (merchant)",
             description="Saves a draft for the caller's business. Send it for approval with POST /my/posts/{id}/submit.")
def create_my_post(payload: MerchantPostCreate = Body(...), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_merchant)):
    return create_my_post_service(db, actor, payload)


@router.get("/my/posts/{post_id}", response_model=BlogManageDetail, summary="One of my posts (merchant)")
def get_my_post(post_id: UUID = Path(...), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_merchant)):
    return get_my_post_service(db, actor, post_id)


@router.patch(
    "/my/posts/{post_id}", response_model=BlogManageDetail, summary="Edit my post (merchant)",
    description=(
        "Allowed while the post is a draft, changes requested, rejected or unpublished. Editing a **published** post sends the edit "
        "for approval at once: the post goes to pending_approval but the old version stays live until the edit is approved. "
        "A post that is pending approval cannot be edited (409)."
    ),
)
def update_my_post(post_id: UUID, payload: MerchantPostUpdate = Body(...), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_merchant)):
    return update_my_post_service(db, actor, post_id, payload)


@router.post("/my/posts/{post_id}/submit", response_model=BlogManageDetail, summary="Send my post for approval (merchant)",
             description="Needs a title, kind, category and body (422 otherwise). Super Admins are notified.")
def submit_my_post(post_id: UUID, db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_merchant)):
    return submit_my_post_service(db, actor, post_id)


@router.post("/my/posts/{post_id}/unpublish", response_model=BlogManageDetail, summary="Take my live post down (merchant)")
def unpublish_my_post(post_id: UUID, payload: ReasonRequest = Body(default_factory=ReasonRequest), db: Session = Depends(get_db),
                      actor: BlogActor = Depends(require_blog_merchant)):
    return unpublish_my_post_service(db, actor, post_id, payload.reason)


@router.post("/my/posts/{post_id}/discard-changes", response_model=BlogManageDetail, summary="Drop sent-back changes of a live post (merchant)",
             description="When an edit of a live post was rejected or sent back, this drops the edit so the post is simply published again.")
def discard_my_changes(post_id: UUID, db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_merchant)):
    return discard_my_changes_service(db, actor, post_id)


@router.delete("/my/posts/{post_id}", summary="Delete my draft (merchant)", description="Drafts only. A live post is unpublished instead.")
def delete_my_post(post_id: UUID, db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_merchant)):
    delete_my_post_service(db, actor, post_id)
    return {"message": "Blog post deleted"}


@router.get("/my/posts/{post_id}/history", response_model=BlogHistoryResponse, summary="History of my post (merchant)")
def my_post_history(post_id: UUID, db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_merchant)):
    return my_post_history_service(db, actor, post_id)


# ---------------------------------------------------------------- Super Admin (registered before /{slug})

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
             description="`status: published` makes it live straight away, with no approval step. Needs a category and a body to publish.")
def create_admin_blog(payload: AdminPostCreate = Body(...), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin)):
    return create_blog_service(db, payload, actor)


@router.get("/admin/posts/{post_id}", response_model=BlogManageDetail, summary="Get a post (Super Admin)")
def get_admin_blog(post_id: UUID = Path(...), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin)):
    return get_admin_post_service(db, actor, post_id)


@router.put("/admin/posts/{post_id}", response_model=BlogManageDetail, summary="Edit a post (Super Admin)",
            description="Applies at once, to a live post too. Refused (409) while a merchant's edit waits for approval; decide that first.")
def update_admin_blog(post_id: UUID, payload: AdminPostUpdate = Body(...), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin)):
    return update_blog_service(db, post_id, payload, actor)


@router.patch("/admin/posts/{post_id}/status", response_model=BlogManageDetail, summary="Publish / unpublish / back to draft (Super Admin)",
              description="Kept for older clients. `archived` means `unpublished`. Prefer /publish, /unpublish and /review.")
def update_admin_blog_status(post_id: UUID, payload: BlogStatusUpdate = Body(...), db: Session = Depends(get_db),
                             actor: BlogActor = Depends(require_blog_super_admin)):
    return update_blog_status_service(db, post_id, payload.status, actor, payload.reason)


@router.post("/admin/posts/{post_id}/publish", response_model=BlogManageDetail, summary="Publish a post now (Super Admin)",
             description="For a draft, unpublished, sent-back or rejected post. A post that is pending approval goes through /review.")
def publish_admin_blog(post_id: UUID, db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin)):
    return publish_blog_service(db, post_id, actor)


@router.post("/admin/posts/{post_id}/unpublish", response_model=BlogManageDetail, summary="Take a live post down (Super Admin)",
             description="A `reason` is required for a merchant's post; the merchant is notified with it.")
def unpublish_admin_blog(post_id: UUID, payload: ReasonRequest = Body(default_factory=ReasonRequest), db: Session = Depends(get_db),
                         actor: BlogActor = Depends(require_blog_super_admin)):
    return unpublish_blog_service(db, post_id, actor, payload.reason)


@router.post("/admin/posts/{post_id}/review", response_model=BlogManageDetail, summary="Approve, request changes or reject (Super Admin)",
             description=(
                 "For a post that is pending approval. `approve` publishes it (and applies a merchant's edit of a live post); "
                 "`request_changes` and `reject` need a `reason`, which the merchant sees on the post and in the notification."
             ))
def review_admin_blog(post_id: UUID, payload: ReviewRequest = Body(...), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin)):
    return review_blog_service(db, post_id, actor, payload.action, payload.reason)


@router.put("/admin/posts/{post_id}/pin", response_model=BlogManageDetail, summary="Pin or unpin a post (Super Admin)",
            description="A pinned post stays at the top of its public list. One on/off switch.")
def pin_admin_blog(post_id: UUID, payload: PinRequest = Body(default_factory=PinRequest), db: Session = Depends(get_db),
                   actor: BlogActor = Depends(require_blog_super_admin)):
    return pin_blog_service(db, post_id, actor, payload.pinned)


@router.delete("/admin/posts/{post_id}", status_code=status.HTTP_200_OK, summary="Delete a post (Super Admin)",
               description="The post is kept for the record but hidden everywhere. `reason` is required for a merchant's post.")
def delete_admin_blog(post_id: UUID, reason: str | None = Query(None, max_length=500), db: Session = Depends(get_db),
                      actor: BlogActor = Depends(require_blog_super_admin)):
    delete_blog_service(db, post_id, actor, reason)
    return {"message": "Blog post deleted"}


@router.get("/admin/posts/{post_id}/history", response_model=BlogHistoryResponse, summary="Who changed what, and when (Super Admin)")
def admin_blog_history(post_id: UUID, db: Session = Depends(get_db), _actor: BlogActor = Depends(require_blog_super_admin)):
    return admin_post_history_service(db, post_id)


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
               description="A category that has posts (409) can only be switched off.")
def delete_admin_category(category_id: UUID, db: Session = Depends(get_db), _actor: BlogActor = Depends(require_blog_super_admin)):
    delete_category_service(db, category_id)
    return {"message": "Category deleted"}


# ---------------------------------------------------------------- one live post (last, so it never shadows the paths above)

@router.get("/{slug}", response_model=BlogDetailResponse, summary="Get a live post by slug (public)",
            description="Counts one view. A post that is not live is a 404. The id also works in place of the slug.")
def get_public_blog_by_slug(slug: str = Path(..., description="Post slug"), db: Session = Depends(get_db)):
    return get_blog_service(db, slug=slug, public_only=True, increment_views=True)
