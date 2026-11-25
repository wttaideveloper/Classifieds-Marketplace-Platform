"""Blog engagement endpoints, mounted under /cms/blogs (docs/blog-engagement.md).

Customers: like, save, comment, reply, edit, delete, report.  Enterprise Admin: counts and comments of their own blogs.  Super Admin: overview, queues, remove / restore, history.
Registered after the blog router; none of these paths can be mistaken for `GET /{slug}` (they all have more than one segment).
"""
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.blog_access import BlogActor, require_blog_merchant, require_blog_super_admin
from app.core.dependencies import get_current_user, get_optional_current_user
from app.db.database import get_db
from app.schemas.blog_engagement_schema import (
    CommentCreate, CommentEdit, CommentLikeState, CommentList, CommentResult, EngagementState, LikeSummary, ReplyPage, MandatoryReason, Queue, ReasonBody, ReportCreate, ReviewRequestBody,
)
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.services import blog_engagement_service as svc

router = APIRouter(tags=["CMS — Blog engagement"])

PAGE = Query(DEFAULT_PAGE, ge=1)
PAGE_SIZE = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)

OUTCOME_DOC = (" The answer is `{outcome, message, comment, comment_id}` with status **201** (published; 200 for an edit), **202** (`held_for_review`: a moderator decides; or `pending_moderation`: "
               "the AI check was down and will be repeated) or **422** (`removed_by_ai`: against the community guidelines, not posted and not shown to anyone but the admins). "
               "Other errors: 422 `COMMENT_EMPTY` / `COMMENT_TOO_LONG` (500 characters), 429 `RATE_LIMITED`, 404 `POST_NOT_FOUND`.")


def _message(outcome: str, *, edit: bool) -> str:
    if edit and outcome == "removed_by_ai":
        return "This edit goes against our community guidelines and was not saved. Your comment is unchanged."
    return {
        "published": "Your comment is updated." if edit else "Your comment is posted.",
        "held_for_review": "Your comment is waiting for a moderator before it is shown.",
        "pending_moderation": "Your comment is waiting to be checked and will be shown once it is.",
        "removed_by_ai": "This comment goes against our community guidelines and was not posted.",
    }[outcome]


def _reply(db: Session, user: dict, comment, code: int, *, edit: bool) -> JSONResponse:
    outcome = "removed_by_ai" if code == 422 else comment.status
    body = CommentResult(outcome=outcome, message=_message(outcome, edit=edit), comment_id=comment.id if not (code == 422 and not edit) else None,
                         comment=None if code == 422 else svc.comment_out(db, comment, user))
    return JSONResponse(status_code=code, content=jsonable_encoder(body))


# ---------------------------------------------------------------- customers: saved blogs (before the /{post_id} routes)

@router.get("/wishlist/my", summary="My saved blogs (customer)",
            description="Live blogs the signed-in user saved, most recently saved first: `{items: [blog list item + saved_at], pagination}`. A blog that was taken down is not listed; saving it again after it is back keeps nothing extra.")
def my_wishlist(page: int = PAGE, page_size: int = PAGE_SIZE, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.my_saved(db, user, page, page_size)


# ---------------------------------------------------------------- customers: like and save

@router.post("/{post_id}/like", response_model=EngagementState, summary="Like a blog (customer)",
             description="Idempotent: liking again changes nothing. Answers the new state and counts. 404 `POST_NOT_FOUND` for a post that is not live, 409 `ENGAGEMENT_NOT_AVAILABLE` for News.")
def like(post_id: str = Path(..., description="The blog's id or its slug"), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.set_like(db, user, post_id, True)


@router.delete("/{post_id}/like", response_model=EngagementState, summary="Remove my like (customer)", description="Idempotent.")
def unlike(post_id: str = Path(..., description="The blog's id or its slug"), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.set_like(db, user, post_id, False)


@router.post("/{post_id}/wishlist", response_model=EngagementState, summary="Save a blog (customer)", description="Idempotent. The save time orders `GET /wishlist/my`.")
def wishlist(post_id: str = Path(..., description="The blog's id or its slug"), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.set_wishlist(db, user, post_id, True)


@router.delete("/{post_id}/wishlist", response_model=EngagementState, summary="Remove a saved blog (customer)", description="Idempotent. Also works after the blog is taken down.")
def unwishlist(post_id: str = Path(..., description="The blog's id or its slug"), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.set_wishlist(db, user, post_id, False)


# ---------------------------------------------------------------- customers: comments

@router.get("/{post_id}/comments", response_model=CommentList, summary="Comments of a blog, with replies (public)",
            description="Top-level comments newest first, each with its `replies` oldest first (one level deep). Only published comments are shown, with a display name such as \"Priya S.\"; a signed-in author "
                        "also sees their own comments that wait for a check (`status: pending_review`). A removed or deleted comment that has replies is a placeholder (`status: unavailable`, `body: null`). "
                        "`can_edit` / `can_delete` / `reported_by_me` are for the signed-in user.")
def list_comments(post_id: str = Path(..., description="The blog's id or its slug"), page: int = PAGE, page_size: int = PAGE_SIZE, db: Session = Depends(get_db), viewer: dict | None = Depends(get_optional_current_user)):
    return svc.list_comments(db, post_id, viewer, page, page_size)


@router.post("/{post_id}/comments", response_model=CommentResult, status_code=201, summary="Comment or reply (customer)",
             description="`parent_id` makes it a reply (a reply to a reply goes under the top-level comment). Checked by the AI before it can be seen." + OUTCOME_DOC,
             responses={202: {"model": CommentResult}, 422: {"model": CommentResult}})
def create_comment(post_id: str = Path(..., description="The blog's id or its slug"), payload: CommentCreate = Body(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    comment, code = svc.create_comment(db, user, post_id, payload.body, payload.parent_id)
    return _reply(db, user, comment, code, edit=False)


@router.get("/{post_id}/likes", response_model=LikeSummary, summary="Likes of a blog (public)",
            description="`{like_count, liked_by_me}`; liked_by_me is null when nobody is signed in. `post_id` is the id or the slug.")
def like_summary(post_id: str = Path(..., description="The blog's id or its slug"), db: Session = Depends(get_db), viewer: dict | None = Depends(get_optional_current_user)):
    return svc.like_summary(db, viewer, post_id)


@router.post("/{post_id}/likes", response_model=LikeSummary, summary="Like a blog (customer)", description="Idempotent. Same as POST /{post_id}/like, in the mobile shape.")
def like_v2(post_id: str = Path(..., description="The blog's id or its slug"), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.set_like_summary(db, user, post_id, True)


@router.delete("/{post_id}/likes", response_model=LikeSummary, summary="Remove my like (customer)", description="Idempotent.")
def unlike_v2(post_id: str = Path(..., description="The blog's id or its slug"), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.set_like_summary(db, user, post_id, False)


@router.get("/{post_id}/comments/{comment_id}/replies", response_model=ReplyPage, summary="Replies of one comment (public)",
            description="The direct replies of the comment, oldest first, each with its own nested replies. For loading a long thread a page at a time; "
                        "the comment list already nests every reply.")
def list_replies(post_id: str = Path(..., description="The blog's id or its slug"), comment_id: UUID = Path(...), page: int = PAGE, page_size: int = PAGE_SIZE,
                 db: Session = Depends(get_db), viewer: dict | None = Depends(get_optional_current_user)):
    return svc.list_replies(db, post_id, comment_id, viewer, page, page_size)


@router.post("/{post_id}/comments/{comment_id}/likes", response_model=CommentLikeState, summary="Like a comment (customer)",
             description="Idempotent. A comment that is not published: 404 COMMENT_NOT_FOUND.")
def like_comment(post_id: str = Path(..., description="The blog's id or its slug"), comment_id: UUID = Path(...), db: Session = Depends(get_db),
                 user: dict = Depends(get_current_user)):
    return svc.set_comment_like(db, user, post_id, comment_id, True)


@router.delete("/{post_id}/comments/{comment_id}/likes", response_model=CommentLikeState, summary="Remove my like from a comment (customer)", description="Idempotent.")
def unlike_comment(post_id: str = Path(..., description="The blog's id or its slug"), comment_id: UUID = Path(...), db: Session = Depends(get_db),
                   user: dict = Depends(get_current_user)):
    return svc.set_comment_like(db, user, post_id, comment_id, False)


@router.patch("/comments/{comment_id}", response_model=CommentResult, summary="Edit my comment or reply (customer)",
              description="Only the author. The new text is checked like a new comment; the comment shows `is_edited` and `edited_at`. 422 means the edit was refused and the comment keeps its old text. "
                          "A published comment whose edit is held goes back to waiting for a moderator." + OUTCOME_DOC,
              responses={202: {"model": CommentResult}, 422: {"model": CommentResult}})
def edit_comment(comment_id: UUID = Path(...), payload: CommentEdit = Body(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    comment, code = svc.edit_comment(db, user, comment_id, payload.body)
    return _reply(db, user, comment, code, edit=True)


@router.delete("/comments/{comment_id}", summary="Delete my comment or reply (customer)",
               description="Soft delete by the author: it disappears (a placeholder stays if it has replies). Idempotent.")
def delete_comment(comment_id: UUID = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.delete_own_comment(db, user, comment_id)


@router.post("/comments/{comment_id}/report", summary="Report a comment (customer)",
             description="One report per person per comment (409 `ALREADY_REPORTED`; 409 `CANNOT_REPORT_OWN`). `reason`: unsafe_medical, harassment, offensive, spam, other. "
                         "One unsafe_medical or harassment report, or three reports of the others, make the AI judge the comment again: it is taken down if it is extreme, held for a moderator if it is borderline, "
                         "and otherwise stays up and waits in the Super Admin's Reported queue.")
def report_comment(comment_id: UUID = Path(...), payload: ReportCreate = Body(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.report_comment(db, user, comment_id, payload.reason, payload.note)


# ---------------------------------------------------------------- Enterprise Admin: own blogs only

@router.get("/my/posts/{post_id}/engagement", summary="Engagement of my blog (Enterprise Admin)",
            description="Counts only, no names: likes, saves, published comments, comments by status, open reports, views and a 30-day trend. 403 for another business's blog.")
def ea_engagement(post_id: UUID = Path(...), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_merchant)):
    return svc.ea_engagement(db, actor, post_id)


@router.get("/my/posts/{post_id}/comments", summary="Comments on my blog (Enterprise Admin)",
            description="Every comment on the blog with its status (`status` takes a comma-separated list). The author is a display name only, never an id.")
def ea_comments(post_id: UUID = Path(...), status: str | None = Query(None), page: int = PAGE, page_size: int = PAGE_SIZE,
                db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_merchant)):
    return svc.ea_comments(db, actor, post_id, status, page, page_size)


@router.delete("/my/comments/{comment_id}", summary="Remove a comment on my blog (Enterprise Admin)",
               description="Removes it from the public view and tells the author. The text is kept for the Super Admin. 403 for a comment on another business's blog; 409 `ALREADY_REMOVED`.")
def ea_remove(comment_id: UUID = Path(...), payload: ReasonBody = Body(default_factory=ReasonBody), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_merchant)):
    return svc.ea_remove(db, actor, comment_id, payload.reason)


@router.post("/my/comments/{comment_id}/request-review", summary="Ask a Super Admin to look at a comment the AI removed (Enterprise Admin)",
             description="Only for a comment the AI removed (409 `NOT_AI_REMOVED`). It appears in the Super Admin's Requests queue and the Super Admins are notified. The Enterprise Admin cannot restore it.")
def ea_request_review(comment_id: UUID = Path(...), payload: ReviewRequestBody = Body(default_factory=ReviewRequestBody), db: Session = Depends(get_db),
                      actor: BlogActor = Depends(require_blog_merchant)):
    return svc.ea_request_review(db, actor, comment_id, payload.note)


# ---------------------------------------------------------------- Super Admin (static paths first, then /{post_id})

@router.get("/admin/engagement", summary="Engagement overview (Super Admin)",
            description="`stats` (blogs, likes, saves, comments, and the sizes of the four queues), `top_blogs` and a paged `items` list of every blog with its counts. `search` matches title, business or author; "
                        "`date_from` / `date_to` limit the likes, saves and comments counted; `status` (comma-separated) picks which comment statuses are counted (default: published).")
def admin_overview(search: str | None = Query(None), status: str | None = Query(None), date_from: date | None = Query(None), date_to: date | None = Query(None),
                   page: int = PAGE, page_size: int = PAGE_SIZE, db: Session = Depends(get_db), _actor: BlogActor = Depends(require_blog_super_admin)):
    return svc.admin_overview(db, search=search, status=status, date_from=date_from, date_to=date_to, page=page, page_size=page_size)


@router.get("/admin/engagement/comments", summary="All comments (Super Admin)",
            description="Every comment and reply with its blog, business, author, status, AI reason and report counts. Filter with `search` (text, author, blog, business), `status` (comma-separated), `post_id`, `tenant_id`.")
def admin_comments(search: str | None = Query(None), status: str | None = Query(None), post_id: UUID | None = Query(None), tenant_id: UUID | None = Query(None),
                   page: int = PAGE, page_size: int = PAGE_SIZE, db: Session = Depends(get_db), _actor: BlogActor = Depends(require_blog_super_admin)):
    return svc.admin_comments(db, search=search, status=status, post_id=post_id, tenant_id=tenant_id, page=page, page_size=page_size)


@router.get("/admin/engagement/ai-moderation", summary="Moderation queues (Super Admin)",
            description="`queue`: `auto_removed` (the AI removed it), `awaiting_review` (held, or waiting for the AI), `user_reports` (reports crossed the threshold) or `ea_requests` (an Enterprise Admin asked for a review). "
                        "Every answer also has `counts` for all four so the tabs can show their numbers. Oldest first; deciding a comment (remove or restore) takes it out of the queue.")
def admin_ai_moderation(queue: Queue = Query("auto_removed"), search: str | None = Query(None), page: int = PAGE, page_size: int = PAGE_SIZE,
                        db: Session = Depends(get_db), _actor: BlogActor = Depends(require_blog_super_admin)):
    return svc.ai_moderation(db, queue, search, page, page_size)


@router.get("/admin/engagement/moderation-history", summary="Moderation history (Super Admin)",
            description="Every moderation event, newest first: who or what (AI, Enterprise Admin, Super Admin), what, why, the status before and after, and a copy of the text judged. Admin eyes only. "
                        "Filter with `comment_id`, `post_id`, `action` (comma-separated).")
def admin_history(comment_id: UUID | None = Query(None), post_id: UUID | None = Query(None), action: str | None = Query(None), page: int = PAGE, page_size: int = PAGE_SIZE,
                  db: Session = Depends(get_db), _actor: BlogActor = Depends(require_blog_super_admin)):
    return svc.moderation_history(db, comment_id=comment_id, post_id=post_id, action=action, page=page, page_size=page_size)


@router.post("/admin/engagement/comments/{comment_id}/remove", summary="Remove a comment (Super Admin)",
             description="`reason` is required. The author is notified. A Super Admin removes or restores; nobody edits a customer's words. 409 `ALREADY_REMOVED`.")
def admin_remove(comment_id: UUID = Path(...), payload: MandatoryReason = Body(...), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin)):
    return svc.admin_remove(db, actor, comment_id, payload.reason)


@router.post("/admin/engagement/comments/{comment_id}/restore", summary="Restore a comment (Super Admin)",
             description="Publishes a removed comment, or one that waits for a moderator, again. `reason` is required and is kept in the history (it records that the AI was overruled). The author is notified. 409 `NOT_RESTORABLE`.")
def admin_restore(comment_id: UUID = Path(...), payload: MandatoryReason = Body(...), db: Session = Depends(get_db), actor: BlogActor = Depends(require_blog_super_admin)):
    return svc.admin_restore(db, actor, comment_id, payload.reason)


@router.get("/admin/engagement/{post_id}", summary="One blog's engagement (Super Admin)",
            description="Counts, comments by status, open reports, a 30-day trend and the ten latest comments (with author ids, which the Super Admin may see).")
def admin_post(post_id: UUID = Path(...), db: Session = Depends(get_db), _actor: BlogActor = Depends(require_blog_super_admin)):
    return svc.admin_post_detail(db, post_id)
