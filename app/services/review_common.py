"""Rules every review module shares (training/course, product, service, event).

One place for: what a valid rating is, how an average is taken, what a moderation action may be and how a
reviewer may be shown. Keeping them here is what makes the modules behave the same way.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from fastapi import HTTPException, Query

MODERATION_STATUSES = ("pending", "approved", "rejected")
MAX_COMMENT_LENGTH = 2000

# Training and event reviews: a Super Admin must say why when rejecting or deleting a review. The reason is kept in the
# moderation history and sent in the notification metadata (feed and realtime event; never in the push payload).
REASON_MODULES = ("training", "product", "service", "event")
# what a Super Admin must explain: rejecting, holding a review back (pending) and deleting someone else's review
REASON_ACTIONS = {"rejected": "reject a review", "pending": "move a review to pending"}
MIN_REASON_LENGTH = 3
MAX_REASON_LENGTH = 500


def clean_reason(raw, what: str) -> str:
    """The moderator's reason, trimmed and on one line. 422 when it is missing, blank, not text, shorter than 3 or longer
    than 500 characters. `what` finishes "A reason is required to ...": "reject a review", "delete a review"."""
    if raw is None:
        raise HTTPException(status_code=422, detail=f"A reason is required to {what}")
    if not isinstance(raw, str):
        raise HTTPException(status_code=422, detail="reason must be text")
    text = " ".join(raw.split())
    if not text:
        raise HTTPException(status_code=422, detail=f"A reason is required to {what}")
    if len(text) < MIN_REASON_LENGTH:
        raise HTTPException(status_code=422, detail=f"The reason must be at least {MIN_REASON_LENGTH} characters")
    if len(text) > MAX_REASON_LENGTH:
        raise HTTPException(status_code=422, detail=f"The reason must be at most {MAX_REASON_LENGTH} characters")
    return text


def parse_rating(raw) -> int | None:
    """A stored rating is text. Only a whole number from 1 to 5 counts; anything else is None."""
    text = str(raw).strip() if raw is not None else ""
    return int(text) if text.isdigit() and 1 <= int(text) <= 5 else None


def validate_rating(raw) -> int:
    """For input: a whole number 1 to 5 (an int, or text such as "4"), else 422."""
    if isinstance(raw, bool):
        raise HTTPException(status_code=422, detail="rating must be a whole number from 1 to 5")
    value = parse_rating(raw)
    if value is None:
        raise HTTPException(status_code=422, detail="rating must be a whole number from 1 to 5")
    return value


def clean_comment(raw) -> str | None:
    """Trim the comment; empty becomes None. Longer than the limit is a 422."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise HTTPException(status_code=422, detail="comment must be text")
    text = raw.strip()
    if len(text) > MAX_COMMENT_LENGTH:
        raise HTTPException(status_code=422, detail=f"comment must be at most {MAX_COMMENT_LENGTH} characters")
    return text or None


def average_rating(ratings: Iterable[int | None]) -> float | None:
    """Mean of the usable ratings rounded to 2 decimals; None (never 0) when there are none."""
    usable = [r for r in ratings if r is not None]
    return round(sum(usable) / len(usable), 2) if usable else None


def rating_distribution(ratings: Iterable[int | None]) -> dict[str, int]:
    """How many reviews gave 5, 4, 3, 2 and 1 stars (always all five keys)."""
    counts = {str(star): 0 for star in range(5, 0, -1)}
    for rating in ratings:
        if rating is not None:
            counts[str(rating)] += 1
    return counts


def validate_action(action) -> str:
    if action not in MODERATION_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid moderation action. Allowed: {sorted(MODERATION_STATUSES)}")
    return action


# --- the life of a review ---------------------------------------------------------------------------------
# A new review is approved and public at once. Only a Super Admin changes a status afterwards. A customer's edit keeps
# the status, except that editing a rejected review sends it back to pending (a resubmission).

NEW_REVIEW_STATUS = "approved"

# A Super Admin deleting someone else's review does not erase it: the row stays with this status, hidden from every
# public count and from the admin tabs, so the author still sees it (GET .../reviews/me, `my_review`) marked as deleted.
# It is not a status a moderator can choose (the moderate calls take approved | rejected | pending); the author can
# delete it for good, or edit it to resubmit it.
DELETED_STATUS = "deleted"

# status -> what the customer sees
STATUS_LABELS = {"approved": "Approved", "pending": "Waiting for Approval", "rejected": "Not Approved", "deleted": "Deleted"}

# what a Super Admin may do to a review in each status
ALLOWED_MOVES = {
    "approved": ("rejected", "pending"),
    "pending": ("approved", "rejected"),
    "rejected": ("approved",),
}


def status_label(status: str | None) -> str:
    return STATUS_LABELS.get(status or "pending", STATUS_LABELS["pending"])


def is_public(status: str | None) -> bool:
    return status == "approved"


def moderation_reason_for(action: str, raw) -> str | None:
    """The cleaned reason for a moderation `action`: required (422 if missing or invalid) for rejected and pending,
    None for approved."""
    phrase = REASON_ACTIONS.get(action)
    return clean_reason(raw, phrase) if phrase else None


def shown_reason(status: str | None, reason: str | None) -> str | None:
    """The reason to show with a review: only while it is rejected, pending or deleted."""
    return reason if reason and status in ("rejected", "pending", DELETED_STATUS) else None


def set_moderation(review, status: str, reason: str | None = None) -> None:
    """A Super Admin's decision: the new status and, for rejected / pending / deleted, the reason. Approving clears it."""
    review.moderation_status = status
    review.moderation_reason = reason if status in ("rejected", "pending", DELETED_STATUS) else None


def after_author_edit(review, previous_status: str | None) -> None:
    """The author edited their review: a rejected or deleted one goes back to pending as a new submission, so the old
    reason is cleared; a pending review stays pending and keeps its reason."""
    review.moderation_status = status_after_edit(previous_status)
    if needs_resubmission(previous_status):
        review.moderation_reason = None


def needs_resubmission(status: str | None) -> bool:
    """A rejected or deleted review is hidden; when its author edits it, it goes back for approval."""
    return status in ("rejected", DELETED_STATUS)


def edit_label(status: str | None) -> str:
    """The button the customer sees on the edit form: a rejected or deleted review is resubmitted, any other is just saved."""
    return "Resubmit Review" if needs_resubmission(status) else "Save Changes"


def status_after_edit(status: str | None) -> str:
    """The status of a review after its author edited it."""
    return "pending" if needs_resubmission(status) else (status or "pending")


def not_deleted(column):
    """SQL condition: the review is not one a Super Admin deleted. For the admin tabs, counts and reports."""
    import sqlalchemy as sa

    return sa.or_(column.is_(None), column != DELETED_STATUS)


def check_move(previous: str | None, action: str) -> bool:
    """Is moving a review from `previous` to `action` a change? False when it is already there (nothing to do).
    409 when a Super Admin may not make that move (a rejected review can only be approved)."""
    if previous == DELETED_STATUS:
        raise HTTPException(status_code=409, detail="A deleted review cannot be moderated. Its author can edit it to resubmit it")
    current = previous if previous in ALLOWED_MOVES else "pending"
    if current == action:
        return False
    if action not in ALLOWED_MOVES[current]:
        allowed = " or ".join(ALLOWED_MOVES[current])
        raise HTTPException(status_code=409, detail=f"A {current} review can only be moved to {allowed}")
    return True


def public_name(value) -> str | None:
    """A name that is safe to show to anyone. An email address is never a display name."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text if text and "@" not in text else None


# --- options for the public review lists ----------------------------------------------------------------
# sort (newest | oldest | highest | lowest), rating (only that many stars), with_comment, and optional paging.
# With no page / page_size the list still returns everything, as before. The average, the count and the star
# breakdown always describe ALL approved reviews, whatever filter or page is asked for.

SORTS = ("newest", "oldest", "highest", "lowest")


@dataclass(frozen=True)
class ListOptions:
    sort: str = "newest"
    rating: int | None = None
    with_comment: bool = False
    page: int | None = None
    page_size: int | None = None

    @property
    def paged(self) -> bool:
        return self.page is not None or self.page_size is not None


def review_list_params(
    sort: str = Query("newest", description="newest (default) | oldest | highest | lowest"),
    rating: int | None = Query(None, ge=1, le=5, description="Only reviews with this many stars"),
    with_comment: bool = Query(False, description="Only reviews that have a comment"),
    page: int | None = Query(None, ge=1, description="Page number. Leave page and page_size out to get every review"),
    page_size: int | None = Query(None, ge=1, le=100, description="Reviews per page (default 20 when paging)"),
) -> ListOptions:
    """FastAPI dependency for the public review lists."""
    if sort not in SORTS:
        raise HTTPException(status_code=422, detail=f"sort must be one of: {', '.join(SORTS)}")
    return ListOptions(sort=sort, rating=rating, with_comment=with_comment, page=page, page_size=page_size)


def apply_list_options(rows, opts: ListOptions, *, rating_of, created_of, comment_of):
    """(rows for this request, pagination dict) after filtering, sorting and paging `rows`."""
    items = list(rows)
    if opts.rating is not None:
        items = [r for r in items if rating_of(r) == opts.rating]
    if opts.with_comment:
        items = [r for r in items if (comment_of(r) or "").strip()]

    items.sort(key=created_of, reverse=(opts.sort != "oldest"))
    if opts.sort == "highest":
        items.sort(key=lambda r: rating_of(r) or 0, reverse=True)   # stable: ties stay newest first
    elif opts.sort == "lowest":
        items.sort(key=lambda r: rating_of(r) or 0)

    total = len(items)
    if not opts.paged:
        return items, {"total": total, "page": 1, "page_size": total, "total_pages": 1 if total else 0}
    page, size = opts.page or 1, opts.page_size or 20
    start = (page - 1) * size
    return items[start:start + size], {"total": total, "page": page, "page_size": size, "total_pages": -(-total // size)}
