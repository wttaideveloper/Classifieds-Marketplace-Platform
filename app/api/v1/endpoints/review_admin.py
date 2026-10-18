"""Review moderation queue across training/course, product, service and event reviews."""
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query, Request
from sqlalchemy.orm import Session

from app.core.catalog_access import get_catalog_access, require_review_moderator
from app.core.dependencies import extract_access_token, get_current_user
from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.review_schema import ReviewAuditResponse, ReviewDeleteBody, ReviewModerateBody, ReviewQueueItem, ReviewQueueResponse
from app.services.review_admin_service import delete_any_review, list_reviews_for_moderation, moderate_any_review
from app.services.review_audit import list_log

router = APIRouter(tags=["Reviews"])


@router.get(
    "/manage",
    response_model=ReviewQueueResponse,
    summary="List reviews to moderate",
    description=(
        "The moderation queue: every review of the items the caller's business owns, whatever its status, newest first, "
        "with `counts` per status for the Pending / Approved / Rejected tabs. An active Super Admin sees every business. "
        "Providers can read the queue but cannot moderate. `module` is training (or course), product, service or event; "
        "leave it out for all of them. Use `review_id` with `PATCH /reviews/{module}/{review_id}/moderate`."
    ),
)
def list_reviews_to_moderate(
    module: str | None = Query(None, description="training | course | product | service | event"),
    status: str | None = Query(None, description="pending | approved | rejected"),
    item_id: UUID | None = Query(None, description="Only reviews of this training / product / service / event"),
    rating: int | None = Query(None, ge=1, le=5, description="Only reviews with this many stars"),
    q: str | None = Query(None, max_length=200, description="Text in the comment"),
    tenant_id: UUID | None = Query(None, description="Only the reviews of this business. A Super Admin can name any business; anyone else only their own"),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    access=Depends(get_catalog_access),
):
    return list_reviews_for_moderation(
        db, access, module=module, status=status, item_id=item_id, rating=rating, q=q, tenant_id=tenant_id,
        page=page, page_size=page_size,
    )


@router.patch(
    "/{module}/{review_id}/moderate",
    response_model=ReviewQueueItem,
    summary="Approve, reject or reset a review (any module)",
    description=(
        "Same rules as the per-module moderate endpoints: Super Admin only (Enterprise Admins and providers get 403). "
        "Allowed moves: approved -> rejected | pending, pending -> approved | rejected, rejected -> approved. Putting a "
        "review to `approved` or `rejected` notifies its author."
        " `reason` is required when rejecting or moving to pending (3 to 500 characters, else 422); it is saved in the moderation history, sent in the notification metadata and shown to the review's author."
    ),
)
def moderate_review(
    request: Request,
    payload: ReviewModerateBody,
    module: str = Path(..., description="training | course | product | service | event"),
    review_id: UUID = Path(...),
    db: Session = Depends(get_db),
    access=Depends(require_review_moderator),
    current_user: dict = Depends(get_current_user),
):
    return moderate_any_review(
        db, module, review_id, payload.action, access, access_token=extract_access_token(request), actor=current_user,
        reason=payload.reason,
    )


@router.delete(
    "/{module}/{review_id}",
    summary="Delete a review (any module)",
    description=(
        "Same rules as the module's own delete: the review's author, or a Super Admin. Deleting someone else's review "
        "is written to the moderation history."
        " To delete someone else's review a Super Admin must send `reason` in the body (or `?reason=`), 3 to 500 characters, else 422; the author sees it."
    ),
)
def delete_review(
    request: Request,
    module: str = Path(..., description="training | course | product | service | event"),
    review_id: UUID = Path(...),
    payload: ReviewDeleteBody | None = Body(None),
    reason: str | None = Query(None, description="The same as the body field, for clients that cannot send a body with DELETE"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return delete_any_review(
        db, module, review_id, current_user, access_token=extract_access_token(request),
        reason=(payload.reason if payload and payload.reason is not None else reason),
    )


@router.get(
    "/audit",
    response_model=ReviewAuditResponse,
    summary="Moderation history",
    description=(
        "Which Super Admin approved, rejected, held back (pending) or deleted which review, and when, newest first, with "
        "the enterprise, the item, the customer, the previous and the new status. An Enterprise Admin sees the history of "
        "their own business (view only); a Super Admin sees every business. Providers are refused. `action` is the new "
        "status: approved | rejected | pending | deleted."
    ),
)
def review_audit(
    module: str | None = Query(None, description="training | product | service | event"),
    review_id: UUID | None = Query(None),
    item_id: UUID | None = Query(None),
    action: str | None = Query(None, description="approved | rejected | pending | deleted"),
    actor_user_id: UUID | None = Query(None, description="Only what this person did"),
    tenant_id: UUID | None = Query(None, description="Only the history of this business (a Super Admin can name any business)"),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    access=Depends(get_catalog_access),
):
    return list_log(
        db, access, module=module, review_id=review_id, item_id=item_id, action=action,
        actor_user_id=actor_user_id, tenant_id=tenant_id, page=page, page_size=page_size,
    )
